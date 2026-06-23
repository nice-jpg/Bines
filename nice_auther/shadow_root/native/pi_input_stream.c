#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

struct packed_event {
  uint16_t type;
  uint16_t code;
  int32_t value;
};

struct replay_input_event {
  struct timeval time;
  uint16_t type;
  uint16_t code;
  int32_t value;
};

static int read_exact(int fd, void *buf, size_t len) {
  char *out = (char *)buf;
  size_t done = 0;
  while (done < len) {
    ssize_t n = read(fd, out + done, len - done);
    if (n == 0) return 0;
    if (n < 0) {
      if (errno == EINTR) continue;
      return -1;
    }
    done += (size_t)n;
  }
  return 1;
}

static int write_exact(int fd, const void *buf, size_t len) {
  const char *in = (const char *)buf;
  size_t done = 0;
  while (done < len) {
    ssize_t n = write(fd, in + done, len - done);
    if (n < 0) {
      if (errno == EINTR) continue;
      return -1;
    }
    done += (size_t)n;
  }
  return 0;
}

static void sleep_us(uint32_t delay_us) {
  struct timespec req;
  req.tv_sec = delay_us / 1000000u;
  req.tv_nsec = (long)(delay_us % 1000000u) * 1000L;
  while (nanosleep(&req, &req) < 0 && errno == EINTR) {}
}

int main(int argc, char **argv) {
  if (argc != 2) {
    fprintf(stderr, "usage: %s /dev/input/eventX\n", argv[0]);
    return 2;
  }

  int out_fd = open(argv[1], O_WRONLY);
  if (out_fd < 0) {
    fprintf(stderr, "open input device failed: %s\n", strerror(errno));
    return 1;
  }

  char magic[8];
  if (read_exact(STDIN_FILENO, magic, sizeof(magic)) != 1 || memcmp(magic, "PIAR1\0\0\0", 8) != 0) {
    fprintf(stderr, "invalid input stream packet\n");
    close(out_fd);
    return 1;
  }

  for (;;) {
    uint32_t frame_header[2];
    int r = read_exact(STDIN_FILENO, frame_header, sizeof(frame_header));
    if (r == 0) break;
    if (r < 0) {
      fprintf(stderr, "read frame header failed: %s\n", strerror(errno));
      close(out_fd);
      return 1;
    }

    uint32_t delay_us = frame_header[0];
    uint32_t event_count = frame_header[1];
    if (event_count == 0 || event_count > 4096) {
      fprintf(stderr, "invalid event count: %u\n", event_count);
      close(out_fd);
      return 1;
    }

    struct packed_event *packed = calloc(event_count, sizeof(struct packed_event));
    struct replay_input_event *events = calloc(event_count, sizeof(struct replay_input_event));
    if (!packed || !events) {
      fprintf(stderr, "allocation failed\n");
      free(packed);
      free(events);
      close(out_fd);
      return 1;
    }

    if (read_exact(STDIN_FILENO, packed, event_count * sizeof(struct packed_event)) != 1) {
      fprintf(stderr, "read frame body failed: %s\n", strerror(errno));
      free(packed);
      free(events);
      close(out_fd);
      return 1;
    }

    for (uint32_t i = 0; i < event_count; i++) {
      memset(&events[i], 0, sizeof(struct replay_input_event));
      events[i].type = packed[i].type;
      events[i].code = packed[i].code;
      events[i].value = packed[i].value;
    }
    free(packed);

    if (delay_us > 0) sleep_us(delay_us);
    if (write_exact(out_fd, events, event_count * sizeof(struct replay_input_event)) < 0) {
      fprintf(stderr, "write input frame failed: %s\n", strerror(errno));
      free(events);
      close(out_fd);
      return 1;
    }
    free(events);
  }

  close(out_fd);
  return 0;
}
