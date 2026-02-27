#!/usr/bin/env python3
# fix_encoder_pullups.py - Test encoder with explicit pullup configuration

import os, sys, time
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_with_pullups():
    """Test encoder with explicit pullup resistors enabled"""
    
    print("=== Testing Encoder with Pullups ===")
    
    try:
        # Try using lgpio directly to set pullups
        import lgpio
        
        # Open GPIO chip
        chip = lgpio.gpiochip_open(0)
        
        clk_pin = 23
        dt_pin = 24  
        sw_pin = 25
        
        print(f"Setting up pins with pullups:")
        print(f"  CLK: GPIO {clk_pin}")
        print(f"  DT:  GPIO {dt_pin}")  
        print(f"  SW:  GPIO {sw_pin}")
        
        # Configure pins as inputs with pullup resistors
        lgpio.gpio_claim_input(chip, clk_pin, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(chip, dt_pin, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(chip, sw_pin, lgpio.SET_PULL_UP)
        
        print("\nReading pin levels with pullups enabled:")
        
        for i in range(10):
            clk = lgpio.gpio_read(chip, clk_pin)
            dt = lgpio.gpio_read(chip, dt_pin)
            sw = lgpio.gpio_read(chip, sw_pin)
            
            print(f"  CLK={clk}, DT={dt}, SW={sw}")
            
            if clk == dt == sw == 0:
                print("  ⚠️  Still all LOW - likely hardware issue")
                break
            elif clk == dt == sw == 1:
                print("  ✓ All HIGH - pullups working!")
                break
            else:
                print("  ✓ Mixed levels - normal operation")
                
            time.sleep(0.5)
        
        # Test rotation detection
        print("\nTesting rotation (turn encoder now):")
        last_clk = last_dt = None
        changes = 0
        
        start_time = time.time()
        while time.time() - start_time < 5.0:  # 5 seconds
            clk = lgpio.gpio_read(chip, clk_pin)
            dt = lgpio.gpio_read(chip, dt_pin)
            
            if clk != last_clk or dt != last_dt:
                changes += 1
                print(f"    Change #{changes}: CLK={clk}, DT={dt}")
                last_clk, last_dt = clk, dt
                
            time.sleep(0.01)
        
        print(f"  Total pin changes detected: {changes}")
        
        # Clean up
        lgpio.gpio_free(chip, clk_pin)
        lgpio.gpio_free(chip, dt_pin)
        lgpio.gpio_free(chip, sw_pin)
        lgpio.gpiochip_close(chip)
        
        return changes > 0
        
    except Exception as e:
        print(f"Pullup test failed: {e}")
        return False

def check_encoder_wiring():
    """Check encoder wiring and suggest fixes"""
    
    print("\n=== Wiring Check ===")
    print("Your encoder should be connected as:")
    print()
    print("Encoder Pin    →    Pi Pin        →    Pi GPIO")
    print("VCC/+          →    Pin 1 (3.3V)  →    3.3V Power")
    print("GND/-          →    Pin 6 (GND)   →    Ground") 
    print("CLK/A          →    Pin 16        →    GPIO 23")
    print("DT/B           →    Pin 18        →    GPIO 24")
    print("SW/Button      →    Pin 22        →    GPIO 25")
    print()
    print("Common Issues:")
    print("1. Missing VCC connection (encoder needs power)")
    print("2. Missing GND connection")
    print("3. Encoder is a 'bare' type without pullups")
    print("4. Loose connections")
    print()
    print("Quick Test:")
    print("- Disconnect encoder completely")
    print("- Run test again - should show 'No encoder' error")
    print("- Reconnect encoder with VCC to 3.3V")

if __name__ == '__main__':
    if test_with_pullups():
        print("\n✓ Encoder responding to rotation!")
        print("Now test the fixed encoder module:")
        print("sudo ./.venv/bin/python debug_encoder.py")
    else:
        print("\n✗ Encoder still not working properly")
        check_encoder_wiring()
