// Board: Seeed Studio XIAO ESP32C3
// Arduino IDE: Tools > USB CDC On Boot > Enabled
// Serial Monitor: 115200 baud, Newline
// Install: Adafruit PWM Servo Driver Library (and its dependencies).
// PCA9685 VCC = 3.3 V. Servo V+ = separate suitable servo supply.
// Connect all grounds together. SDA = GPIO6, SCL = GPIO7.

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm(0x40, Wire);

// Braille dots: left 1,2,3; right 4,5,6.
constexpr uint8_t CHANNELS[6] = {0, 1, 2, 6, 5, 4};
// Your original pulse widths. Check linkage travel before testing.
constexpr uint16_t REST_US[6] = {2000, 2000, 2000, 1000, 1000, 1000};
constexpr uint16_t RAISED_US[6] = {1000, 1000, 1000, 2000, 2000, 2000};
constexpr uint32_t HOLD_MS = 3000;

// Bits 0..5 represent dots 1..6, respectively.
constexpr uint8_t LETTERS[26] = {
  1, 3, 9, 25, 17, 11, 27, 19, 10, 26,
  5, 7, 13, 29, 21, 15, 31, 23, 14, 30,
  37, 39, 58, 45, 61, 53
};

bool driverReady = false;
uint32_t lastRetry = 0;
uint32_t lastStatus = 0;
char input[16];
uint8_t inputLength = 0;
bool inputOverflow = false;

bool addressResponds() {
  Wire.beginTransmission(0x40);
  return Wire.endTransmission() == 0;
}

void printPrompt() {
  Serial.println("Enter one lowercase letter (a-z).");
  Serial.println("Or send ? for status, ! to return all dots to rest.");
}

void setCell(uint8_t mask) {
  // Set EVERY dot, including those absent from the new letter.
  for (uint8_t i = 0; i < 6; ++i) {
    pwm.writeMicroseconds(CHANNELS[i],
      (mask & (1u << i)) ? RAISED_US[i] : REST_US[i]);
    delay(250);
  }
}

void tryDriver() {
  Serial.println("Checking PCA9685 at 0x40...");
  if (!addressResponds()) {
    Serial.println("No I2C reply. Check VCC, GND, SDA and SCL. Retrying.");
    return;
  }
  Serial.println("Direct I2C check: responds.");
  if (!pwm.begin()) {
    Serial.println("Driver initialization failed. Retrying.");
    return;
  }
  pwm.setPWMFreq(50);
  // Disable all outputs until a valid command is received.
  for (uint8_t channel = 0; channel < 16; ++channel) {
    pwm.setPWM(channel, 0, 4096);
  }
  driverReady = true;
  Serial.println("PCA9685 ready. Turn on servo power before sending a letter.");
  printPrompt();
}

void handleCommand() {
  if (inputOverflow) {
    Serial.println("Command too long. Send one letter per line.");
    return;
  }
  if (inputLength == 0) return;
  if (inputLength != 1) {
    printPrompt();
    return;
  }
  char c = input[0];
  if (c == '?') {
    Serial.println(driverReady ? "USB serial alive; PCA9685 initialized."
                               : "USB serial alive; waiting for PCA9685.");
    return;
  }
  if (!driverReady) {
    Serial.println("PCA9685 not ready. No servo command sent.");
    return;
  }
  if (c == '!') {
    setCell(0);
    Serial.println("Back at rest.");
    return;
  }
  if (c < 'a' || c > 'z') {
    printPrompt();
    return;
  }
  setCell(0);
  delay(1000);
  Serial.print("Showing ");
  Serial.print(c);
  Serial.println(" on braille display.");
  setCell(LETTERS[c - 'a']);
  delay(HOLD_MS);
  setCell(0);
  // Preserve the completion message used by the Braille Vision web app.
  Serial.println("Back at rest.");
  printPrompt();
}

void setup() {
  Serial.begin(115200);
  uint32_t start = millis();
  while (!Serial && millis() - start < 5000) delay(10);
  Serial.println("\nBoot OK: Braille serial starting.");
  Wire.begin(6, 7, 100000);
  Wire.setTimeOut(50);
  delay(500);
  tryDriver();
  lastRetry = millis();
  lastStatus = millis();
}

void loop() {
  if (!driverReady && millis() - lastRetry >= 2000) {
    lastRetry = millis();
    tryDriver();
  }
  // Repeated output makes a late-opened Serial Monitor easy to diagnose.
  if (millis() - lastStatus >= 5000) {
    lastStatus = millis();
    Serial.println(driverReady ? "Alive: ready for a-z + Enter."
                               : "Alive: waiting for PCA9685 at 0x40.");
  }
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') {
      handleCommand();
      inputLength = 0;
      inputOverflow = false;
    } else if (inputLength < sizeof(input) - 1) {
      input[inputLength++] = c;
    } else {
      inputOverflow = true;
    }
  }
  delay(1);
}
