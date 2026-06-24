package gateway

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

type Config struct {
	ListenHost       string
	ListenPort       int
	ICEPublicIP      string
	ICEUDPPortMin    int
	ICEUDPPortMax    int
	Transport        string
	RTPListenHost    string
	RTPPort          int
	AgentControlPort int
	EventsURL        string
	EventsToken      string
}

type SessionDescription struct {
	Type string `json:"type"`
	SDP  string `json:"sdp"`
}

type ErrorResponse struct {
	OK    bool   `json:"ok"`
	Error string `json:"error"`
}

type Gateway struct {
	Config      Config
	Client      *http.Client
	api         *webrtc.API
	videoTrack  *webrtc.TrackLocalStaticRTP
	rtpConn     net.PacketConn
	tcpListener net.Listener
	rtpOnce     sync.Once
	rtpStartErr error
	controlAddr *net.UDPAddr
	controlMu   sync.Mutex
	controlConn net.Conn
}

func New(config Config) *Gateway {
	api, track := newWebRTCAPIAndTrack(config)
	return &Gateway{
		Config:     config,
		Client:     &http.Client{Timeout: 5 * time.Second},
		api:        api,
		videoTrack: track,
	}
}

func (g *Gateway) Answer(offer SessionDescription) (SessionDescription, error) {
	log.Printf("webrtc offer received transport=%s sdp_bytes=%d", g.Config.Transport, len(offer.SDP))
	if offer.Type != "offer" || offer.SDP == "" {
		return SessionDescription{}, errors.New("offer sdp and type are required")
	}
	if err := g.startRTPForwarder(); err != nil {
		return SessionDescription{}, err
	}
	pc, err := g.api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return SessionDescription{}, err
	}
	sender, err := pc.AddTrack(g.videoTrack)
	if err != nil {
		_ = pc.Close()
		return SessionDescription{}, err
	}
	go g.readRTCP(sender)
	pc.OnDataChannel(func(channel *webrtc.DataChannel) {
		channel.OnMessage(func(message webrtc.DataChannelMessage) {
			_ = g.ForwardEvents(message.Data)
		})
	})
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offer.SDP}); err != nil {
		_ = pc.Close()
		return SessionDescription{}, err
	}
	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		_ = pc.Close()
		return SessionDescription{}, err
	}
	gatherComplete := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(answer); err != nil {
		_ = pc.Close()
		return SessionDescription{}, err
	}
	<-gatherComplete
	local := pc.LocalDescription()
	if local == nil {
		_ = pc.Close()
		return SessionDescription{}, errors.New("WebRTC local description was not created")
	}
	log.Printf("webrtc answer created sdp_bytes=%d", len(local.SDP))
	return SessionDescription{Type: "answer", SDP: local.SDP}, nil
}

func (g *Gateway) StartMedia() error {
	return g.startRTPForwarder()
}

func (g *Gateway) ForwardEvents(payload []byte) error {
	return g.ForwardAgentControl(payload)
}

func (g *Gateway) ForwardAgentControl(payload []byte) error {
	if len(payload) == 0 {
		return nil
	}
	if len(payload) > 0xffff {
		return errors.New("agent control payload is too large")
	}
	g.controlMu.Lock()
	defer g.controlMu.Unlock()
	if g.controlConn == nil {
		return errors.New("android agent control connection is not ready")
	}
	header := []byte{byte(len(payload) >> 8), byte(len(payload))}
	if _, err := g.controlConn.Write(header); err != nil {
		return err
	}
	if _, err := g.controlConn.Write(payload); err != nil {
		return err
	}
	return nil
}

func (g *Gateway) Close() error {
	if g.tcpListener != nil {
		_ = g.tcpListener.Close()
	}
	if g.rtpConn != nil {
		return g.rtpConn.Close()
	}
	return nil
}

func (g *Gateway) startRTPForwarder() error {
	g.rtpOnce.Do(func() {
		transport := g.Config.Transport
		if transport == "" {
			transport = "adb_reverse_tcp"
		}
		if g.Config.AgentControlPort > 0 {
			g.controlAddr, _ = net.ResolveUDPAddr("udp", fmt.Sprintf("127.0.0.1:%d", g.Config.AgentControlPort))
		}
		if transport == "adb_reverse_tcp" || transport == "tcp_direct" {
			g.rtpStartErr = g.startTCPForwarder()
			return
		}
		rtpListenHost := g.Config.RTPListenHost
		if rtpListenHost == "" {
			rtpListenHost = g.Config.ListenHost
		}
		addr := fmt.Sprintf("%s:%d", rtpListenHost, g.Config.RTPPort)
		g.rtpConn, g.rtpStartErr = net.ListenPacket("udp", addr)
		if g.rtpStartErr != nil {
			return
		}
		log.Printf("rtp udp listening addr=%s", addr)
		go g.forwardRTP()
	})
	return g.rtpStartErr
}

func (g *Gateway) startTCPForwarder() error {
	listenHost := "127.0.0.1"
	if g.Config.Transport == "tcp_direct" {
		listenHost = g.Config.RTPListenHost
		if listenHost == "" {
			listenHost = "0.0.0.0"
		}
	}
	addr := fmt.Sprintf("%s:%d", listenHost, g.Config.RTPPort)
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	g.tcpListener = listener
	log.Printf("rtp tcp listening addr=%s", addr)
	go g.acceptTCPRTP()
	return nil
}

func (g *Gateway) acceptTCPRTP() {
	for {
		conn, err := g.tcpListener.Accept()
		if err != nil {
			log.Printf("rtp tcp accept end error=%v", err)
			return
		}
		log.Printf("rtp tcp accepted remote=%s", conn.RemoteAddr())
		go g.forwardTCPRTP(conn)
	}
}

func (g *Gateway) forwardTCPRTP(conn net.Conn) {
	defer conn.Close()
	g.setControlConn(conn)
	defer g.clearControlConn(conn)
	header := make([]byte, 2)
	packets := 0
	bytesForwarded := 0
	started := time.Now()
	defer func() {
		log.Printf("rtp tcp closed remote=%s packets=%d bytes=%d duration_ms=%d", conn.RemoteAddr(), packets, bytesForwarded, time.Since(started).Milliseconds())
	}()
	for {
		if _, err := io.ReadFull(conn, header); err != nil {
			log.Printf("rtp tcp read header error remote=%s error=%v", conn.RemoteAddr(), err)
			return
		}
		length := int(binary.BigEndian.Uint16(header))
		if length <= 0 || length > 65535 {
			return
		}
		payload := make([]byte, length)
		if _, err := io.ReadFull(conn, payload); err != nil {
			log.Printf("rtp tcp read payload error remote=%s length=%d error=%v", conn.RemoteAddr(), length, err)
			return
		}
		if len(payload) < 12 || payload[0] != 0x80 {
			log.Printf("rtp tcp invalid raw remote=%s length=%d head=%s", conn.RemoteAddr(), length, hexPrefix(payload, 16))
			continue
		}
		var packet rtp.Packet
		if err := packet.Unmarshal(payload); err != nil {
			log.Printf("rtp tcp packet unmarshal error remote=%s length=%d head=%s error=%v", conn.RemoteAddr(), length, hexPrefix(payload, 16), err)
			continue
		}
		packets++
		bytesForwarded += length
		if packets == 1 || packets == 30 || packets%300 == 0 {
			log.Printf("rtp tcp forwarded remote=%s packets=%d bytes=%d seq=%d timestamp=%d marker=%t", conn.RemoteAddr(), packets, bytesForwarded, packet.SequenceNumber, packet.Timestamp, packet.Marker)
		}
		_ = g.videoTrack.WriteRTP(&packet)
	}
}

func (g *Gateway) setControlConn(conn net.Conn) {
	g.controlMu.Lock()
	defer g.controlMu.Unlock()
	g.controlConn = conn
	log.Printf("agent control attached remote=%s", conn.RemoteAddr())
}

func (g *Gateway) clearControlConn(conn net.Conn) {
	g.controlMu.Lock()
	defer g.controlMu.Unlock()
	if g.controlConn == conn {
		g.controlConn = nil
		log.Printf("agent control detached remote=%s", conn.RemoteAddr())
	}
}

func hexPrefix(data []byte, limit int) string {
	if len(data) < limit {
		limit = len(data)
	}
	out := make([]byte, 0, limit*3)
	const digits = "0123456789abcdef"
	for i := 0; i < limit; i++ {
		if i > 0 {
			out = append(out, ' ')
		}
		value := data[i]
		out = append(out, digits[value>>4], digits[value&0x0f])
	}
	return string(out)
}

func (g *Gateway) forwardRTP() {
	buf := make([]byte, 1600)
	for {
		n, _, err := g.rtpConn.ReadFrom(buf)
		if err != nil {
			return
		}
		var packet rtp.Packet
		if err := packet.Unmarshal(buf[:n]); err != nil {
			continue
		}
		if packet.SequenceNumber%300 == 0 {
			log.Printf("rtp udp forwarded bytes=%d seq=%d timestamp=%d marker=%t", n, packet.SequenceNumber, packet.Timestamp, packet.Marker)
		}
		_ = g.videoTrack.WriteRTP(&packet)
	}
}

func (g *Gateway) readRTCP(sender *webrtc.RTPSender) {
	for {
		packets, _, err := sender.ReadRTCP()
		if err != nil {
			return
		}
		for _, packet := range packets {
			if _, ok := packet.(*rtcp.PictureLossIndication); ok {
				log.Printf("rtcp pli received")
				g.requestIDR()
			}
		}
	}
}

func (g *Gateway) requestIDR() {
	if err := g.ForwardAgentControl([]byte("PLI")); err == nil {
		return
	}
	if g.controlAddr == nil {
		return
	}
	conn, err := net.DialUDP("udp", nil, g.controlAddr)
	if err != nil {
		return
	}
	defer conn.Close()
	_, _ = conn.Write([]byte("PLI"))
}

func newWebRTCAPIAndTrack(config Config) (*webrtc.API, *webrtc.TrackLocalStaticRTP) {
	codec := webrtc.RTPCodecCapability{
		MimeType:     webrtc.MimeTypeH264,
		ClockRate:    90000,
		SDPFmtpLine:  "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
		RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}},
	}
	mediaEngine := &webrtc.MediaEngine{}
	_ = mediaEngine.RegisterCodec(webrtc.RTPCodecParameters{
		RTPCodecCapability: codec,
		PayloadType:        96,
	}, webrtc.RTPCodecTypeVideo)
	settingEngine := webrtc.SettingEngine{}
	settingEngine.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	if config.ICEPublicIP != "" {
		settingEngine.SetNAT1To1IPs([]string{config.ICEPublicIP}, webrtc.ICECandidateTypeHost)
		log.Printf("webrtc ice public ip=%s", config.ICEPublicIP)
	}
	if config.ICEUDPPortMin > 0 || config.ICEUDPPortMax > 0 {
		if config.ICEUDPPortMin <= 0 || config.ICEUDPPortMax <= 0 {
			panic("both ICE UDP port min and max are required")
		}
		if err := settingEngine.SetEphemeralUDPPortRange(uint16(config.ICEUDPPortMin), uint16(config.ICEUDPPortMax)); err != nil {
			panic(err)
		}
		log.Printf("webrtc ice udp port range=%d-%d", config.ICEUDPPortMin, config.ICEUDPPortMax)
	}
	api := webrtc.NewAPI(webrtc.WithMediaEngine(mediaEngine), webrtc.WithSettingEngine(settingEngine))
	track, err := webrtc.NewTrackLocalStaticRTP(codec, "video", "screen")
	if err != nil {
		panic(err)
	}
	return api, track
}

func (g *Gateway) ServeOffer(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var offer SessionDescription
	if err := json.NewDecoder(r.Body).Decode(&offer); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	answer, err := g.Answer(offer)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotImplemented)
		_ = json.NewEncoder(w).Encode(ErrorResponse{OK: false, Error: err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(answer)
}

func (g *Gateway) ServeIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(indexHTML))
}

func (g *Gateway) ServeStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	g.controlMu.Lock()
	controlReady := g.controlConn != nil
	g.controlMu.Unlock()
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":        true,
		"recording": false,
		"video": map[string]any{
			"backend":   "webrtc_h264",
			"transport": g.Config.Transport,
			"rtp_port":  g.Config.RTPPort,
		},
		"webrtc_gateway": map[string]any{
			"running":       true,
			"control_ready": controlReady,
			"listen_host":   g.Config.ListenHost,
			"listen_port":   g.Config.ListenPort,
			"ice_public_ip": g.Config.ICEPublicIP,
		},
	})
}

func (g *Gateway) ServeEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	payload, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := g.ForwardAgentControl(payload); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(ErrorResponse{OK: false, Error: err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

func (g *Gateway) ServeWake(w http.ResponseWriter, r *http.Request) {
	payload := []byte(`{"type":"wake"}`)
	if err := g.ForwardAgentControl(payload); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(ErrorResponse{OK: false, Error: err.Error()})
		return
	}
	_ = g.ForwardAgentControl([]byte("PLI"))
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}
