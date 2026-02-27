#!/usr/bin/env python3
# debug_ui.py - Monitor UI state changes

import os, sys, time
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

def debug_ui_state():
    """Debug UI state transitions"""
    
    print("=== UI State Debug ===")
    print("This shows exactly what the UI is doing")
    print()
    
    # Enable all debug output
    os.environ['OTPI_DEBUG_ENCODER_EVENTS'] = '1'
    os.environ['PYTHONUNBUFFERED'] = '1'
    
    try:
        # Mock OLED for testing
        class MockOLED:
            def __init__(self):
                self.width = 128
                self.height = 64
                print("[MOCK OLED] Initialized")
        
        # Mock encoder 
        class MockEncoder:
            def __init__(self):
                self._steps = 0
                self._pressed = False
                print("[MOCK ENCODER] Initialized")
                
            def steps(self):
                # Simulate occasional steps for testing
                if time.time() % 10 < 0.1:  # Brief step every 10 seconds
                    return 1
                return 0
                
            def pressed(self):
                # Simulate occasional button press
                if time.time() % 15 < 0.1:  # Brief press every 15 seconds  
                    if not self._pressed:
                        self._pressed = True
                        return True
                else:
                    self._pressed = False
                return False
        
        # Mock canvas context manager
        class MockCanvas:
            def __init__(self, device):
                self.device = device
                
            def __enter__(self):
                return MockDraw()
                
            def __exit__(self, *args):
                pass
        
        class MockDraw:
            def text(self, pos, text, fill=1):
                print(f"[MOCK OLED] Draw: {text} at {pos}")
        
        # Patch the luma imports
        sys.modules['luma.core.render'] = type('MockModule', (), {'canvas': MockCanvas})
        
        # Now import our UI
        from oled_ui import OledUI, ResetAction
        
        # Initialize UI
        oled = MockOLED()
        encoder = MockEncoder()
        ui = OledUI(oled, 0.33, 50)  # hue=0.33, brightness=50%
        
        print(f"UI initialized on screen {ui.screen}")
        print()
        print("Monitoring UI state changes...")
        print("The UI will automatically simulate some inputs for testing")
        print("Press Ctrl+C to stop")
        print("-" * 50)
        
        last_screen = ui.screen
        last_hue = ui.hue
        last_brightness = ui.user_pct
        
        for frame in range(1000):  # Run for ~50 seconds at 50ms per frame
            try:
                # Mock TOTP data
                code = "123456"
                secs_left = 30 - (frame % 60)  # Countdown
                
                # Call UI handler
                hue, brightness, action = ui.handle(encoder, code, secs_left)
                
                # Check for state changes
                changes = []
                if ui.screen != last_screen:
                    changes.append(f"Screen: {last_screen} → {ui.screen}")
                    last_screen = ui.screen
                    
                if abs(hue - last_hue) > 0.01:
                    changes.append(f"Hue: {last_hue:.3f} → {hue:.3f}")
                    last_hue = hue
                    
                if brightness != last_brightness:
                    changes.append(f"Brightness: {last_brightness}% → {brightness}%")
                    last_brightness = brightness
                    
                if action != ResetAction.NONE:
                    changes.append(f"Action: {action}")
                
                # Print changes
                if changes:
                    print(f"Frame {frame:4d}: {' | '.join(changes)}")
                
                # Show periodic status
                if frame % 200 == 0:  # Every 10 seconds
                    print(f"Frame {frame:4d}: Screen={ui.screen}, Hue={hue:.3f}, Bright={brightness}%, Action={action}")
                
            except Exception as e:
                print(f"Frame {frame}: Error: {e}")
                import traceback
                traceback.print_exc()
                break
                
            time.sleep(0.05)  # 50ms per frame
            
    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as e:
        print(f"UI debug failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    debug_ui_state()
