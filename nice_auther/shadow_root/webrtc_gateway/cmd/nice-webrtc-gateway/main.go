package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"

	gateway "bines/nice_auther/shadow_root/webrtc_gateway"
)

func main() {
	listenHost := flag.String("listen-host", "127.0.0.1", "HTTP signaling listen host")
	listenPort := flag.Int("listen-port", 9765, "HTTP signaling listen port")
	icePublicIP := flag.String("ice-public-ip", "", "IP advertised in WebRTC ICE candidates")
	iceUDPPortMin := flag.Int("ice-udp-port-min", 0, "minimum UDP port for WebRTC ICE")
	iceUDPPortMax := flag.Int("ice-udp-port-max", 0, "maximum UDP port for WebRTC ICE")
	transport := flag.String("transport", "adb_reverse_tcp", "Android-to-gateway media transport: adb_reverse_tcp, tcp_direct, or udp_rtp")
	rtpListenHost := flag.String("rtp-listen-host", "0.0.0.0", "H.264 RTP listen host")
	rtpPort := flag.Int("rtp-port", 9766, "H.264 RTP listen port")
	agentControlPort := flag.Int("agent-control-port", 9767, "Android agent control port")
	eventsURL := flag.String("events-url", "", "deprecated: browser controls are forwarded directly to the Android agent")
	eventsToken := flag.String("events-token", "", "deprecated")
	flag.Parse()

	server := gateway.New(gateway.Config{
		ListenHost:       *listenHost,
		ListenPort:       *listenPort,
		ICEPublicIP:      *icePublicIP,
		ICEUDPPortMin:    *iceUDPPortMin,
		ICEUDPPortMax:    *iceUDPPortMax,
		Transport:        *transport,
		RTPListenHost:    *rtpListenHost,
		RTPPort:          *rtpPort,
		AgentControlPort: *agentControlPort,
		EventsURL:        *eventsURL,
		EventsToken:      *eventsToken,
	})
	if err := server.StartMedia(); err != nil {
		log.Fatal(err)
	}
	defer server.Close()
	mux := http.NewServeMux()
	mux.HandleFunc("/", server.ServeIndex)
	mux.HandleFunc("/offer", server.ServeOffer)
	mux.HandleFunc("/webrtc/offer", server.ServeOffer)
	mux.HandleFunc("/status", server.ServeStatus)
	mux.HandleFunc("/events", server.ServeEvents)
	mux.HandleFunc("/wake", server.ServeWake)
	addr := fmt.Sprintf("%s:%d", *listenHost, *listenPort)
	log.Fatal(http.ListenAndServe(addr, mux))
}
