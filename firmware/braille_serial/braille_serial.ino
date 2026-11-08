#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm(0x40);

// Channels in Braille dot order: 1, 2, 3 on the left; 4, 5, 6 on the right.
constexpr uint8_t CHANNELS[] = {0, 1, 2, 6, 5, 4};
constexpr uint8_t DOT_COUNT = sizeof(CHANNELS) / sizeof(CHANNELS[0]);

constexpr uint8_t D1 = 1u << 0;
constexpr uint8_t D2 = 1u << 1;
constexpr uint8_t D3 = 1u << 2;
constexpr uint8_t D4 = 1u << 3;
constexpr uint8_t D5 = 1u << 4;
constexpr uint8_t D6 = 1u << 5;

struct LetterPattern {
  char letter;
  uint8_t dots;
};

constexpr LetterPattern LETTERS[] = {
  {'a', D1},
  {'b', D1 | D2},
  {'c', D1 | D4},
  {'d', D1 | D4 | D5},
  {'e', D1 | D5},
  {'f', D1 | D2 | D4},
  {'g', D1 | D2 | D4 | D5},
  {'h', D1 | D2 | D5},
  {'i', D2 | D4},
  {'j', D2 | D4 | D5},
  {'k', D1 | D3},
  {'l', D1 | D2 | D3},
  {'m', D1 | D3 | D4},
  {'n', D1 | D3 | D4 | D5},
  {'o', D1 | D3 | D5},
  {'p', D1 | D2 | D3 | D4},
  {'q', D1 | D2 | D3 | D4 | D5},
  {'r', D1 | D2 | D3 | D5},
  {'s', D2 | D3 | D4},
  {'t', D2 | D3 | D4 | D5},
  {'u', D1 | D3 | D6},
  {'v', D1 | D2 | D3 | D6},
  {'w', D2 | D4 | D5 | D6},
  {'x', D1 | D3 | D4 | D6},
  {'y', D1 | D3 | D4 | D5 | D6},
  {'z', D1 | D3 | D5 | D6},
};

uint8_t findDots(char letter) {
  for (const LetterPattern& pattern : LETTERS) {
    if (pattern.letter == letter) {
      return pattern.dots;
    }
  }
  return 0;
}

void setDots(uint8_t dots, bool display) {
  for (uint8_t i = 0; i < DOT_COUNT; ++i) {
    if (dots & (1u << i)) {
      const bool rightColumn = i >= 3;
      const uint16_t restUs = rightColumn ? 1000 : 2000;
      const uint16_t displayUs = rightColumn ? 2000 : 1000;
      pwm.writeMicroseconds(CHANNELS[i], display ? displayUs : restUs);
      delay(250);
    }
  }
}

void printPrompt() {
  Serial.println("Enter one lowercase letter (a-z).");
}

void setup() {
  Serial.begin(115200);
  Wire.begin(6, 7, 100000);  // SDA, SCL, I2C speed

  if (!pwm.begin()) {
    while (true) {
      Serial.println("PCA9685 did not respond at 0x40.");
      delay(1000);
    }
  }

  pwm.setPWMFreq(50);

  // Clear any active PWM settings left over from an earlier sketch.
  for (uint8_t channel = 0; channel < 16; ++channel) {
    pwm.setPWM(channel, 0, 4096);
  }

  Serial.println("Turn on servo power before sending a letter.");
  printPrompt();
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();

  const uint8_t dots = command.length() == 1 ? findDots(command[0]) : 0;
  if (dots == 0) {
    printPrompt();
    return;
  }

  setDots(dots, false);
  delay(1000);

  Serial.printf("Showing %c on channels:", command[0]);
  for (uint8_t i = 0; i < DOT_COUNT; ++i) {
    if (dots & (1u << i)) {
      Serial.printf(" %u", static_cast<unsigned>(CHANNELS[i]));
    }
  }
  Serial.println();

  setDots(dots, true);
  delay(3000);

  setDots(dots, false);
  Serial.println("Back at rest.");
  printPrompt();
}