# Native components for HILHarness.
#   bootprobe - portable C boot-log parser
#   can_node  - SocketCAN ECU node (Linux only; builds a stub elsewhere)
CC      ?= cc
CFLAGS  ?= -Wall -Wextra -Werror -O2

UNAME_S := $(shell uname -s)

ifeq ($(UNAME_S),Linux)
all: bootprobe can_node
else
all: bootprobe
	@echo "native: $(UNAME_S) detected - can_node skipped (SocketCAN is Linux-only)"
endif

.PHONY: all bootprobe can_node clean

bootprobe: bootprobe.c
	$(CC) $(CFLAGS) -o $@ $<

can_node: can_node.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f bootprobe can_node
