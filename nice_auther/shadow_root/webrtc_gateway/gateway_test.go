package gateway

import (
	"bytes"
	"encoding/json"
	"io"
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

func TestForwardEventsPostsPayload(t *testing.T) {
	var receivedBody string
	var receivedToken string
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() != "http://127.0.0.1:8765/events" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		defer r.Body.Close()
		buf, _ := io.ReadAll(r.Body)
		receivedBody = string(buf)
		receivedToken = r.Header.Get("X-Shadow-Token")
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(nil))}, nil
	})}
	gateway := New(Config{EventsURL: "http://127.0.0.1:8765/events", EventsToken: "secret"})
	gateway.Client = client

	if err := gateway.ForwardEvents([]byte(`{"events":[{"type":"pointerup"}]}`)); err != nil {
		t.Fatal(err)
	}
	if receivedBody != `{"events":[{"type":"pointerup"}]}` {
		t.Fatalf("unexpected forwarded payload: %s", receivedBody)
	}
	if receivedToken != "secret" {
		t.Fatalf("missing token header")
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return fn(r)
}
