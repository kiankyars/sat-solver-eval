CXX := g++
CC  := gcc
AR  := ar

CXXFLAGS := -std=c++20 -O3 -Wall -march=native -DNDEBUG
CFLAGS   := -O3 -Wall -march=native -DNDEBUG
CPPFLAGS := -Isrc/cliquer

SAT_SRC := src/satsuma.cpp
SAT_OBJ := $(SAT_SRC:.cpp=.o)

CLIQUER_SRC := \
	src/cliquer/cliquer.c \
	src/cliquer/graph.c \
	src/cliquer/reorder.c

CLIQUER_OBJ := $(CLIQUER_SRC:.c=.o)
CLIQUER_LIB := libcliquer.a

CPPFLAGS += -DCLIQUES=1
LDLIBS   += $(CLIQUER_LIB)

.PHONY: all clean

all: satsuma

satsuma: $(SAT_OBJ) $(CLIQUER_LIB)
	$(CXX) $(CXXFLAGS) -o $@ $(SAT_OBJ) $(LDLIBS)

$(CLIQUER_LIB): $(CLIQUER_OBJ)
	$(AR) rcs $@ $^

%.o: %.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

%.o: %.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

clean:
	rm -f satsuma $(SAT_OBJ) $(CLIQUER_OBJ) $(CLIQUER_LIB)
