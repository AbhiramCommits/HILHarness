# Native components for HILHarness (Step 2: bootprobe).
CC      ?= cc
CFLAGS  ?= -Wall -Wextra -Werror -O2

.PHONY: all clean

all: bootprobe

bootprobe: bootprobe.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f bootprobe
