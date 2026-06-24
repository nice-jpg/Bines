package gateway

import (
	"encoding/binary"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/pion/webrtc/v4"
)

func TestAnswerValidatesOffer(t *testing.T) {
	gateway := New(Config{RTPPort: 9001, AgentControlPort: 9002})

	_, err := gateway.Answer(SessionDescription{Type: "answer", SDP: ""})
	if err == nil || !strings.Contains(err.Error(), "offer sdp and type are required") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestNewGatewayRegistersH264Track(t *testing.T) {
	gateway := New(Config{})

	if gateway.videoTrack.Codec().MimeType != webrtc.MimeTypeH264 {
		t.Fatalf("unexpected codec: %#v", gateway.videoTrack.Codec())
	}
}

func TestServeOfferRejectsInvalidOfferAsJson(t *testing.T) {
	gateway := New(Config{})
	body := `{"type":"answer","sdp":""}`
	req := httptest.NewRequest(http.MethodPost, "/offer", strings.NewReader(body))
	recorder := httptest.NewRecorder()

	gateway.ServeOffer(recorder, req)

	if recorder.Code != http.StatusNotImplemented {
		t.Fatalf("unexpected status: %d", recorder.Code)
	}
	var errorResponse ErrorResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &errorResponse); err != nil {
		t.Fatal(err)
	}
	if errorResponse.OK || !strings.Contains(errorResponse.Error, "offer sdp and type") {
		t.Fatalf("unexpected error response: %#v", errorResponse)
	}
}

func TestForwardEventsWritesLengthPrefixedAgentControlPayload(t *testing.T) {
	gatewaySide, agentSide := net.Pipe()
	defer gatewaySide.Close()
	defer agentSide.Close()
	gateway := New(Config{})
	gateway.setControlConn(gatewaySide)
	payloadCh := make(chan []byte, 1)
	errCh := make(chan error, 1)
	go func() {
		header := make([]byte, 2)
		if _, err := io.ReadFull(agentSide, header); err != nil {
			errCh <- err
			return
		}
		length := int(binary.BigEndian.Uint16(header))
		payload := make([]byte, length)
		if _, err := io.ReadFull(agentSide, payload); err != nil {
			errCh <- err
			return
		}
		payloadCh <- payload
	}()

	if err := gateway.ForwardEvents([]byte(`{"events":[{"type":"pointerup"}]}`)); err != nil {
		t.Fatal(err)
	}
	var payload []byte
	select {
	case err := <-errCh:
		t.Fatal(err)
	case payload = <-payloadCh:
	}
	if string(payload) != `{"events":[{"type":"pointerup"}]}` {
		t.Fatalf("unexpected forwarded payload: %s", payload)
	}
}
