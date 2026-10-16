/*
 * can_node.c - a SocketCAN ECU node for HILHarness.
 *
 * Transmits periodic infotainment frames, answers diagnostic requests, and
 * supports the same fault-injection flags as the Python fallback node:
 *   --drop-rate R --stale-signal S --bad-dlc N --out-of-range S --silence-ms N
 *
 * SocketCAN is Linux-only; on other platforms this compiles to a stub that
 * prints a clear message and exits 0, so `make -C native` stays green.
 */
#if defined(__linux__)

#define _POSIX_C_SOURCE 200809L
/* struct ifreq / IFNAMSIZ in <net/if.h> are BSD/misc extensions that glibc
 * hides under a strict _POSIX_C_SOURCE; _DEFAULT_SOURCE re-exposes them. */
#define _DEFAULT_SOURCE

#include <errno.h>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define ID_VEH_SPEED 0x100
#define ID_HVAC 0x200
#define ID_DISPLAY 0x300
#define ID_AUDIO 0x400
#define ID_BT 0x500
#define ID_MUX 0x600
#define ID_DIAG_REQ 0x7DF
#define ID_DIAG_RESP 0x7E8

struct sched_entry {
    uint32_t id;
    double period_s;
    double next;
};

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void sleep_us(long us) {
    struct timespec ts;
    ts.tv_sec = us / 1000000L;
    ts.tv_nsec = (us % 1000000L) * 1000L;
    nanosleep(&ts, NULL);
}

/* Little-endian (Intel/start-bit) signal packing into a CAN payload. */
static void set_signal(uint8_t *data, int start, int len, uint32_t value) {
    for (int i = 0; i < len; i++) {
        int bit = start + i;
        int byte = bit / 8;
        int off = bit % 8;
        uint32_t v = (value >> i) & 1u;
        if (v)
            data[byte] |= (uint8_t)(1u << off);
        else
            data[byte] &= (uint8_t)~(1u << off);
    }
}

static double rand_frac(void) {
    return (double)rand() / ((double)RAND_MAX + 1.0);
}

int main(int argc, char **argv) {
    const char *iface = "can0";
    double drop_rate = 0.0;
    const char *stale_signal = NULL;
    const char *out_of_range = NULL;
    int bad_dlc = -1;
    int silence_ms = 0;
    double duration = 0.0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--iface") == 0 && i + 1 < argc)
            iface = argv[++i];
        else if (strcmp(argv[i], "--drop-rate") == 0 && i + 1 < argc)
            drop_rate = atof(argv[++i]);
        else if (strcmp(argv[i], "--stale-signal") == 0 && i + 1 < argc)
            stale_signal = argv[++i];
        else if (strcmp(argv[i], "--out-of-range") == 0 && i + 1 < argc)
            out_of_range = argv[++i];
        else if (strcmp(argv[i], "--bad-dlc") == 0 && i + 1 < argc)
            bad_dlc = atoi(argv[++i]);
        else if (strcmp(argv[i], "--silence-ms") == 0 && i + 1 < argc)
            silence_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--duration") == 0 && i + 1 < argc)
            duration = atof(argv[++i]);
    }

    int s = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (s < 0) {
        perror("socket");
        return 1;
    }
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(s, SIOCGIFINDEX, &ifr) < 0) {
        fprintf(stderr, "can_node: interface %s not found: %s\n", iface, strerror(errno));
        close(s);
        return 1;
    }
    struct sockaddr_can addr;
    memset(&addr, 0, sizeof(addr));
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(s);
        return 1;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    srand(1234);

    struct sched_entry sched[] = {
        {ID_VEH_SPEED, 0.020, 0.0}, {ID_MUX, 0.050, 0.0},  {ID_HVAC, 0.100, 0.0},
        {ID_DISPLAY, 0.100, 0.0},   {ID_AUDIO, 0.100, 0.0}, {ID_BT, 0.200, 0.0},
    };
    const size_t nsched = sizeof(sched) / sizeof(sched[0]);

    double start = now_s();
    double silence_start = start + 0.4;
    double silence_end = silence_start + (double)silence_ms / 1000.0;
    for (size_t i = 0; i < nsched; i++)
        sched[i].next = start;
    uint8_t counter = 0;

    while (!g_stop) {
        double t = now_s();
        if (duration > 0.0 && t - start >= duration)
            break;

        /* --- receive diagnostics (non-blocking) --- */
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(s, &rfds);
        struct timeval tv;
        tv.tv_sec = 0;
        tv.tv_usec = 1000;
        if (select(s + 1, &rfds, NULL, NULL, &tv) > 0 && FD_ISSET(s, &rfds)) {
            struct can_frame rx;
            ssize_t n = read(s, &rx, sizeof(rx));
            if (n == (ssize_t)sizeof(rx) && (rx.can_id & CAN_EFF_MASK) == ID_DIAG_REQ) {
                struct can_frame tx;
                memset(&tx, 0, sizeof(tx));
                tx.can_id = ID_DIAG_RESP;
                tx.can_dlc = 8;
                tx.data[0] = (uint8_t)((rx.data[0] + 0x40) & 0xFF);
                tx.data[1] = rx.data[1];
                tx.data[2] = rx.data[2];
                tx.data[3] = 0x12;
                tx.data[4] = 0x34;
                tx.data[5] = rx.data[3];
                if (write(s, &tx, sizeof(tx)) != (ssize_t)sizeof(tx))
                    perror("write diag response");
            }
        }

        int in_silence = (silence_ms > 0) && (t >= silence_start && t < silence_end);
        for (size_t i = 0; i < nsched; i++) {
            if (t < sched[i].next)
                continue;
            sched[i].next = t + sched[i].period_s;

            uint8_t data[8];
            memset(data, 0, sizeof(data));
            if (sched[i].id == ID_VEH_SPEED) {
                counter = (uint8_t)((counter + 1) % 16);
                uint32_t speed_raw = 6000; /* 60.00 km/h */
                if (stale_signal && strcmp(stale_signal, "VehicleSpeed") == 0)
                    speed_raw = 4200;
                if (out_of_range && strcmp(out_of_range, "VehicleSpeed") == 0)
                    speed_raw = 30000; /* 300.00 km/h, above DBC max */
                set_signal(data, 0, 16, speed_raw);
                set_signal(data, 16, 4, 3); /* GearPos = Drive */
                set_signal(data, 45, 4, counter);
            } else if (sched[i].id == ID_HVAC) {
                set_signal(data, 0, 8, 44);  /* SetpointTemp 22.0 */
                set_signal(data, 8, 8, 42);  /* CabinTemp 21.0 */
                set_signal(data, 16, 4, 3);  /* FanSpeed */
                set_signal(data, 20, 1, 1);  /* ACOn */
            } else if (sched[i].id == ID_DISPLAY) {
                set_signal(data, 0, 8, 80); /* Brightness */
                set_signal(data, 8, 2, 1);  /* Theme */
            } else if (sched[i].id == ID_AUDIO) {
                set_signal(data, 0, 7, 30); /* Volume */
                set_signal(data, 7, 3, 2);  /* AudioSource=USB */
            } else if (sched[i].id == ID_BT) {
                set_signal(data, 0, 2, 2); /* BtState=Connected */
                set_signal(data, 2, 4, 3); /* PairedDevices */
            } else if (sched[i].id == ID_MUX) {
                set_signal(data, 0, 8, 2);  /* Mux=2 */
                set_signal(data, 8, 8, 50); /* AmbientLight 50% */
            }

            if (in_silence)
                continue;
            if (drop_rate > 0.0 && rand_frac() < drop_rate)
                continue;

            struct can_frame frame;
            memset(&frame, 0, sizeof(frame));
            frame.can_id = sched[i].id;
            int dlc = 8;
            if (bad_dlc >= 0 && bad_dlc < 8)
                dlc = bad_dlc;
            frame.can_dlc = (uint8_t)dlc;
            memcpy(frame.data, data, (size_t)dlc);
            if (write(s, &frame, sizeof(frame)) < 0)
                perror("write");
        }
        sleep_us(500);
    }

    close(s);
    return 0;
}

#else /* !__linux__ */

#include <stdio.h>

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    fprintf(stderr, "can_node: SocketCAN is Linux-only; skipping (use the Python node)\n");
    return 0;
}

#endif /* __linux__ */
