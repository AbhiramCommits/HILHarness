/*
 * bootprobe.c - parse a kernel-style boot log and emit timing + fault JSON.
 *
 * Usage:  bootprobe [FILE]      (reads stdin when FILE is omitted)
 *
 * It understands the structured markers emitted by the simulated target and by
 * real DUT agents:
 *   [  t] hil-stage bootloader begin
 *   [  t] hil-stage kernel begin
 *   [  t] hil-stage userspace begin
 *   [  t] hil-stage userspace ready
 *   [  t] hil-driver <name> probe begin
 *   [  t] hil-driver <name> probe <ms> ms
 *   [  t] hil-driver <name> probe FAIL
 *   [  t] hil-driver <name> probe timeout
 *
 * Output: a single JSON object on stdout. Exit 0 on success, 1 if the input is
 * malformed (no parseable timestamps / no boot stages), never crashes on
 * truncated or corrupt input.
 */
#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DRIVERS 256
#define MAX_FAULTS 128
#define NAME_LEN 64
#define LINE_LEN 512

typedef struct {
    char name[NAME_LEN];
    double ms;
} Driver;

static Driver drivers[MAX_DRIVERS];
static int n_drivers = 0;

static char failed[MAX_DRIVERS][NAME_LEN];
static int n_failed = 0;

static char timedout[MAX_DRIVERS][NAME_LEN];
static int n_timedout = 0;

static char faults[MAX_FAULTS][LINE_LEN];
static int n_faults = 0;

static int has_bootloader = 0, has_kernel = 0, has_userspace = 0, has_ready = 0;
static double t_bootloader = 0, t_kernel = 0, t_userspace = 0, t_ready = 0;
static double first_ts = 0, last_ts = 0;
static int have_first = 0, n_ts = 0;
static long line_count = 0;

/* case-insensitive substring search */
static int contains_ci(const char *hay, const char *needle) {
    size_t nlen = strlen(needle);
    if (nlen == 0)
        return 0;
    for (const char *p = hay; *p; ++p) {
        size_t i = 0;
        while (i < nlen && p[i] &&
               tolower((unsigned char)p[i]) == tolower((unsigned char)needle[i]))
            i++;
        if (i == nlen)
            return 1;
    }
    return 0;
}

static void json_escape(FILE *out, const char *s) {
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        switch (*p) {
        case '"':
            fputs("\\\"", out);
            break;
        case '\\':
            fputs("\\\\", out);
            break;
        case '\n':
            fputs("\\n", out);
            break;
        case '\r':
            fputs("\\r", out);
            break;
        case '\t':
            fputs("\\t", out);
            break;
        default:
            if (*p < 0x20)
                fprintf(out, "\\u%04x", *p);
            else
                fputc(*p, out);
        }
    }
}

static int add_driver(const char *name, double ms) {
    if (n_drivers >= MAX_DRIVERS)
        return 0;
    snprintf(drivers[n_drivers].name, NAME_LEN, "%s", name);
    drivers[n_drivers].ms = ms;
    n_drivers++;
    return 1;
}

static int add_failed(const char *name) {
    if (n_failed >= MAX_DRIVERS)
        return 0;
    snprintf(failed[n_failed++], NAME_LEN, "%s", name);
    return 1;
}

static int add_timeout(const char *name) {
    if (n_timedout >= MAX_DRIVERS)
        return 0;
    snprintf(timedout[n_timedout++], NAME_LEN, "%s", name);
    return 1;
}

static void add_fault(const char *line) {
    if (n_faults >= MAX_FAULTS)
        return;
    snprintf(faults[n_faults++], LINE_LEN, "%s", line);
}

/* Returns 1 and sets *ts if the line starts with "[ <float>]". */
static int parse_ts(const char *line, double *ts) {
    char *end = NULL;
    if (line[0] != '[')
        return 0;
    double v = strtod(line + 1, &end);
    if (end == line + 1)
        return 0;
    if (*end != ']')
        return 0;
    *ts = v;
    return 1;
}

/* tokenize a mutable copy of msg; returns count, fills toks[] */
static int tokenize(char *msg, char *toks[], int max) {
    int n = 0;
    char *save = NULL;
    for (char *t = strtok_r(msg, " \t", &save); t && n < max;
         t = strtok_r(NULL, " \t", &save))
        toks[n++] = t;
    return n;
}

static void handle_stage(const char *msg, double ts) {
    char buf[LINE_LEN];
    snprintf(buf, sizeof(buf), "%s", msg);
    char *toks[8];
    int n = tokenize(buf, toks, 8);
    if (n < 3)
        return;
    if (strcmp(toks[2], "begin") == 0) {
        if (strcmp(toks[1], "bootloader") == 0 && !has_bootloader) {
            has_bootloader = 1;
            t_bootloader = ts;
        } else if (strcmp(toks[1], "kernel") == 0 && !has_kernel) {
            has_kernel = 1;
            t_kernel = ts;
        } else if (strcmp(toks[1], "userspace") == 0 && !has_userspace) {
            has_userspace = 1;
            t_userspace = ts;
        }
    } else if (strcmp(toks[2], "ready") == 0 && strcmp(toks[1], "userspace") == 0) {
        has_ready = 1;
        t_ready = ts;
    }
}

static void handle_driver(const char *msg, double ts) {
    (void)ts;
    char buf[LINE_LEN];
    snprintf(buf, sizeof(buf), "%s", msg);
    char *toks[8];
    int n = tokenize(buf, toks, 8);
    /* hil-driver <name> probe <verb|ms> [ms] */
    if (n < 4 || strcmp(toks[0], "hil-driver") != 0 || strcmp(toks[2], "probe") != 0)
        return;
    const char *name = toks[1];
    const char *verb = toks[3];
    if (strcmp(verb, "FAIL") == 0) {
        add_failed(name);
    } else if (strcmp(verb, "timeout") == 0) {
        add_timeout(name);
    } else if (strcmp(verb, "begin") == 0) {
        /* in-flight; recorded only if it later shows a result */
    } else {
        char *end = NULL;
        double ms = strtod(verb, &end);
        if (end != verb && *end == '\0')
            add_driver(name, ms);
    }
}

static void process_line(const char *line) {
    line_count++;
    const char *nl = strchr(line, '\n');
    char clean[LINE_LEN];
    size_t len = nl ? (size_t)(nl - line) : strlen(line);
    if (len >= sizeof(clean))
        len = sizeof(clean) - 1;
    memcpy(clean, line, len);
    clean[len] = '\0';

    if (contains_ci(clean, "fail") || contains_ci(clean, "timeout") ||
        contains_ci(clean, "oops") || contains_ci(clean, "panic"))
        add_fault(clean);

    double ts = 0;
    if (!parse_ts(clean, &ts))
        return;
    n_ts++;
    if (!have_first) {
        first_ts = ts;
        have_first = 1;
    }
    last_ts = ts;

    const char *msg = clean;
    const char *close = strchr(clean, ']');
    if (close)
        msg = close + 1;
    while (*msg == ' ' || *msg == '\t')
        msg++;

    if (strncmp(msg, "hil-stage", 9) == 0)
        handle_stage(msg, ts);
    else if (strncmp(msg, "hil-driver", 10) == 0)
        handle_driver(msg, ts);
}

static void emit_json(int malformed) {
    double end = has_ready ? t_ready : last_ts;
    double start = has_bootloader ? t_bootloader : first_ts;
    double total = (end - start) * 1000.0;
    if (total < 0)
        total = 0;

    printf("{\n");
    printf("  \"total_boot_ms\": %.3f,\n", total);
    printf("  \"stages_ms\": {");
    double bl = (has_bootloader && has_kernel) ? (t_kernel - t_bootloader) * 1000.0 : 0;
    double kern = (has_kernel && has_userspace) ? (t_userspace - t_kernel) * 1000.0 : 0;
    double user = (has_userspace) ? (end - t_userspace) * 1000.0 : 0;
    printf("\"bootloader\": %.3f, \"kernel\": %.3f, \"userspace\": %.3f},\n", bl, kern,
           user);

    printf("  \"drivers_ms\": {");
    for (int i = 0; i < n_drivers; i++) {
        printf("%s\"", i ? ", " : "");
        json_escape(stdout, drivers[i].name);
        printf("\": %.3f", drivers[i].ms);
    }
    printf("},\n");

    printf("  \"failed_drivers\": [");
    for (int i = 0; i < n_failed; i++)
        printf("%s\"%s\"", i ? ", " : "", failed[i]);
    printf("],\n");

    printf("  \"timeout_drivers\": [");
    for (int i = 0; i < n_timedout; i++)
        printf("%s\"%s\"", i ? ", " : "", timedout[i]);
    printf("],\n");

    printf("  \"fault_lines\": [");
    for (int i = 0; i < n_faults; i++) {
        printf("%s\"", i ? ", " : "");
        json_escape(stdout, faults[i]);
        printf("\"");
    }
    printf("],\n");

    printf("  \"has_bootloader\": %s,\n", has_bootloader ? "true" : "false");
    printf("  \"has_kernel\": %s,\n", has_kernel ? "true" : "false");
    printf("  \"has_userspace\": %s,\n", has_userspace ? "true" : "false");
    printf("  \"has_ready\": %s,\n", has_ready ? "true" : "false");
    printf("  \"line_count\": %ld,\n", line_count);
    printf("  \"timestamp_count\": %d,\n", n_ts);
    printf("  \"malformed\": %s\n", malformed ? "true" : "false");
    printf("}\n");
}

int main(int argc, char **argv) {
    FILE *in = stdin;
    if (argc > 1 && strcmp(argv[1], "-") != 0) {
        in = fopen(argv[1], "r");
        if (!in) {
            fprintf(stderr, "bootprobe: cannot open %s\n", argv[1]);
            return 1;
        }
    }

    char *line = NULL;
    size_t cap = 0;
    ssize_t n;
    while ((n = getline(&line, &cap, in)) != -1) {
        (void)n;
        process_line(line);
    }
    free(line);
    if (in != stdin)
        fclose(in);

    /* malformed = nothing parseable at all, or no boot stages found */
    int malformed = (n_ts == 0) || (!has_bootloader && !has_kernel && !has_userspace);
    emit_json(malformed);
    return malformed ? 1 : 0;
}
