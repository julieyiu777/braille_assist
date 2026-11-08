// Braille Vision: 6-servo braille cell driver (Arduino Uno / Nano / Mega)
//
// Serial protocol, 115200 baud, sent by the Braille Vision web app:
//   0x40..0x7F  one braille cell; bits 0..5 = dots 1..6 (0x40 = blank cell / space)
//   '\n'        end of word: lower all dots after the last cell has been shown
//   '!'         clear: drop anything queued and lower all dots (sent before each new word)
//   '#<ms>\n'   set how long each cell is held, e.g. "#1200\n"
//
// Wiring: dot servo signal pins below. Power the servos from a separate 5 V supply
// (not the Arduino 5 V pin) and connect that supply's ground to Arduino GND.
//
//   dot 1 (o .)  dot 4 (. o)
//   dot 2 (o .)  dot 5 (. o)
//   dot 3 (o .)  dot 6 (. o)

#include <Servo.h>

const uint8_t PINS[6] = {3, 5, 6, 9, 10, 11};  // dot 1..6
const uint8_t UP_DEG = 60;                      // raised position: tune for your linkage
const uint8_t DOWN_DEG = 0;                     // lowered position
const uint8_t END_OF_WORD = 0x80;

Servo dots[6];
uint8_t q[96];
uint8_t qHead = 0, qTail = 0;
unsigned long dwellMs = 1200, shownAt = 0;
bool holding = false;
char cmd[8];
uint8_t cmdLen = 0;
bool inCmd = false;

void setCell(uint8_t mask) {
  for (uint8_t i = 0; i < 6; i++) dots[i].write(((mask >> i) & 1) ? UP_DEG : DOWN_DEG);
}

void enqueue(uint8_t v) {
  uint8_t next = (qTail + 1) % sizeof(q);
  if (next != qHead) { q[qTail] = v; qTail = next; }  // drop when full
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < 6; i++) dots[i].attach(PINS[i]);
  setCell(0);
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    uint8_t c = Serial.read();
    if (inCmd) {
      if (c == '\n') {
        cmd[cmdLen] = 0;
        dwellMs = constrain(atol(cmd), 200, 10000);
        inCmd = false;
        Serial.print("DWELL "); Serial.println(dwellMs);
      } else if (cmdLen < sizeof(cmd) - 1) {
        cmd[cmdLen++] = c;
      }
    } else if (c == '#') {
      inCmd = true; cmdLen = 0;
    } else if (c == '!') {
      qHead = qTail = 0; holding = false; setCell(0);
    } else if (c == '\n') {
      enqueue(END_OF_WORD);
    } else if (c >= 0x40 && c <= 0x7F) {
      enqueue(c & 0x3F);
    }
  }

  if (holding && millis() - shownAt < dwellMs) return;
  if (qHead == qTail) return;

  uint8_t v = q[qHead];
  qHead = (qHead + 1) % sizeof(q);
  if (v == END_OF_WORD) {
    setCell(0); holding = false;
    Serial.println("DONE");
  } else {
    setCell(v); holding = true; shownAt = millis();
    Serial.print("CELL "); Serial.println(v);
  }
}
