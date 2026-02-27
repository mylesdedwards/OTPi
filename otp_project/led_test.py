#!/usr/bin/env python3
# led_test.py - Test maximum LED power draw

import time
import os

# Use your existing LED configuration
LED_PIN_BCM = int(os.environ.get("OTPI_LED_PIN", 18))
LED_COUNT = 151  # Your total LED count
TEST_BRIGHTNESS = 0.80  # power level

def _board_pin_from_bcm(bcm: int):
    import board
    m = {
        18: getattr(board, "D18", None),
        10: getattr(board, "D10", None),
        12: getattr(board, "D12", None),
        21: getattr(board, "D21", None),
    }
    return m.get(bcm) or getattr(board, "D18")

def test_max_power():
    try:
        import neopixel
        
        pin = _board_pin_from_bcm(LED_PIN_BCM)
        
        # Initialize strip at 80% brightness
        strip = neopixel.NeoPixel(
            pin, LED_COUNT, auto_write=False,
            pixel_order=neopixel.GRB,
            brightness=TEST_BRIGHTNESS
        )
        
        print(f"Setting all {LED_COUNT} LEDs to bright white at {TEST_BRIGHTNESS*100}% power...")
        print("This represents maximum theoretical power draw.")
        print("Press Ctrl+C to stop")
        
        # Set all LEDs to bright white (255, 255, 255)
        strip.fill((255, 255, 255))
        strip.show()
        
        # Keep them on until interrupted
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nTurning off LEDs...")
        strip.fill((0, 0, 0))
        strip.show()
        strip.deinit()
        print("Test complete")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    test_max_power()
