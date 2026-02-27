#!/usr/bin/env python3
# clear_oled.py - Force clear OLED display

import sys

def clear_oled():
    """Force clear the OLED display"""
    print("Attempting to clear OLED...")
    
    try:
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306, sh1106
        from luma.core.render import canvas
        
        # Try both common addresses and device types
        configs = [
            (1, 0x3C, ssd1306),
            (1, 0x3D, ssd1306),
            (1, 0x3C, sh1106),
            (1, 0x3D, sh1106),
        ]
        
        for bus, addr, device_class in configs:
            try:
                print(f"Trying {device_class.__name__} at 0x{addr:02X}...")
                serial = i2c(port=bus, address=addr)
                oled = device_class(serial)
                
                # Clear display
                with canvas(oled) as draw:
                    pass  # Empty canvas = clear screen
                    
                print("✓ OLED cleared successfully!")
                return True
                
            except Exception as e:
                if "timeout" in str(e).lower():
                    print(f"✗ Timeout at 0x{addr:02X} - device busy")
                else:
                    print(f"- No device at 0x{addr:02X}")
                continue
                
        print("✗ Could not access OLED")
        return False
        
    except ImportError as e:
        print(f"✗ Missing OLED libraries: {e}")
        return False

if __name__ == '__main__':
    clear_oled()
