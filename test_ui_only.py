#!/usr/bin/env python3
# test_ui_only.py - Test just the OLED UI without LEDs

import os, sys, time
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_ui_only():
    """Test UI functionality without LED interference"""
    
    print("=== UI Only Test ===")
    print("Testing OLED UI navigation without LEDs")
    print()
    
    # Set debug environment
    os.environ['OTPI_DEBUG_ENCODER_EVENTS'] = '1'
    os.environ['PYTHONUNBUFFERED'] = '1'
    
    try:
        # Initialize OLED
        print("[DEBUG] Step 1: Initializing OLED")
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306
        
        oled = None
        for addr in [0x3C, 0x3D]:
            try:
                serial = i2c(port=1, address=addr)
                oled = ssd1306(serial)
                print(f"[DEBUG] OLED initialized at 0x{addr:02X}")
                break
            except Exception as e:
                print(f"[DEBUG] OLED address 0x{addr:02X} failed: {e}")
                
        if not oled:
            print("[DEBUG] No OLED found, exiting")
            return False
            
        # Initialize encoder
        print("[DEBUG] Step 2: Initializing encoder")
        from encoder import Encoder
        encoder = Encoder()
        print(f"[DEBUG] Encoder backend: {encoder.backend_name()}")
        
        # Initialize UI
        print("[DEBUG] Step 3: Initializing UI")
        from oled_ui import OledUI, ResetAction
        ui = OledUI(oled, 0.33, 50)  # hue=0.33, brightness=50%
        print(f"[DEBUG] UI initialized on screen {ui.screen}")
        
        # Generate fake TOTP for testing
        print("[DEBUG] Step 4: Starting UI test loop")
        print("Rotate encoder and press button to test navigation")
        print("Press Ctrl+C to stop")
        print("-" * 40)
        
        frame = 0
        last_screen = ui.screen
        
        while True:
            frame += 1
            
            # Fake TOTP data
            code = f"{frame % 1000000:06d}"  # Changing 6-digit code
            secs_left = 30 - (frame % 30)    # Countdown timer
            
            # Call UI handler
            try:
                hue, brightness, action = ui.handle(encoder, code, secs_left)
                
                # Check for screen changes
                if ui.screen != last_screen:
                    print(f"[DEBUG] Screen changed: {last_screen} → {ui.screen}")
                    last_screen = ui.screen
                    
                # Check for reset actions
                if action != ResetAction.NONE:
                    print(f"[DEBUG] Reset action: {action}")
                    # Don't actually perform reset in test
                    
            except Exception as e:
                print(f"[DEBUG] UI handle error: {e}")
                import traceback
                traceback.print_exc()
                break
                
            # Status every 5 seconds
            if frame % 100 == 0:  # 50ms * 100 = 5 seconds
                print(f"[DEBUG] Frame {frame}: Screen={ui.screen}, Hue={hue:.2f}, Bright={brightness}%")
                
            time.sleep(0.05)  # 50ms per frame
            
    except KeyboardInterrupt:
        print("\n[DEBUG] Stopped by user")
        return True
    except Exception as e:
        print(f"[DEBUG] UI test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            if 'encoder' in locals():
                encoder.close()
        except:
            pass

if __name__ == '__main__':
    test_ui_only()
