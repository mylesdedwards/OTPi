#!/usr/bin/env python3
# test_encoder.py - Test encoder functionality independently

import os, sys, time
from pathlib import Path

# Check if we're in the virtual environment
def check_venv():
    """Ensure we're running in the virtual environment"""
    project_dir = Path(__file__).parent
    venv_python = project_dir / ".venv" / "bin" / "python"
    
    if not venv_python.exists():
        print(f"Error: Virtual environment not found at {venv_python}")
        print("Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt")
        sys.exit(1)
    
    # Check if we're already running in venv
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        return True  # Already in venv
    
    # Check if we have the required modules
    try:
        import luma.oled.device
        import neopixel
        return True  # Modules available
    except ImportError:
        print(f"Error: Not running in virtual environment or missing packages")
        print(f"Run: sudo {venv_python} {' '.join(sys.argv)}")
        sys.exit(1)

# Check environment before importing project modules
check_venv()

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_encoder():
    """Test encoder functionality"""
    print("=== Encoder Test ===")
    
    try:
        from encoder import Encoder
        
        print("Initializing encoder...")
        enc = Encoder()
        print(f"Backend: {enc.backend_name()}")
        
        # Test raw pin levels
        try:
            levels = enc.raw_levels()
            if levels:
                clk, dt, sw = levels
                print(f"Raw levels - CLK: {clk}, DT: {dt}, SW: {sw}")
            else:
                print("Raw levels not available")
        except Exception as e:
            print(f"Raw levels error: {e}")
        
        print("\nTesting encoder (rotate and press to test):")
        print("Press Ctrl+C to exit")
        
        last_time = time.time()
        step_count = 0
        press_count = 0
        
        while True:
            try:
                steps = enc.steps()
                pressed = enc.pressed()
                
                if steps:
                    step_count += abs(steps)
                    direction = "CW" if steps > 0 else "CCW" 
                    print(f"Steps: {steps:+3d} ({direction}), Total: {step_count}")
                
                if pressed:
                    press_count += 1
                    print(f"Button pressed! Count: {press_count}")
                
                # Show periodic status
                now = time.time()
                if now - last_time > 5.0:
                    print(f"Status: {step_count} steps, {press_count} presses in {now-last_time:.1f}s")
                    last_time = now
                    
            except Exception as e:
                print(f"Encoder read error: {e}")
            
            time.sleep(0.01)  # 100Hz sampling
            
    except KeyboardInterrupt:
        print("\nTest stopped by user")
    except Exception as e:
        print(f"Encoder test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            enc.close()
        except:
            pass
    
    return True

def test_oled():
    """Test OLED functionality"""
    print("\n=== OLED Test ===")
    
    try:
        # Try to initialize OLED
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306
        from luma.core.render import canvas
        
        print("Scanning for OLED...")
        for addr in [0x3C, 0x3D]:
            try:
                serial = i2c(port=1, address=addr)
                oled = ssd1306(serial)
                print(f"OLED found at address 0x{addr:02X}")
                
                # Test display
                with canvas(oled) as draw:
                    draw.text((0, 0), "OLED Test", fill=1)
                    draw.text((0, 16), f"Address: 0x{addr:02X}", fill=1)
                    draw.text((0, 32), "Test successful!", fill=1)
                
                time.sleep(2)
                
                # Clear display
                with canvas(oled) as draw:
                    pass
                
                return True
                
            except Exception as e:
                print(f"Address 0x{addr:02X} failed: {e}")
                continue
                
        print("No OLED found")
        return False
        
    except Exception as e:
        print(f"OLED test failed: {e}")
        return False

def test_leds():
    """Test LED functionality"""
    print("\n=== LED Test ===")
    
    try:
        from led_display import _Strip
        
        print("Initializing LED strip...")
        strip = _Strip(151, 0.3, 18)  # 151 LEDs, 30% brightness, pin 18
        
        print("Testing colors...")
        
        # Test sequence
        colors = [
            (255, 0, 0),    # Red
            (0, 255, 0),    # Green  
            (0, 0, 255),    # Blue
            (255, 255, 0),  # Yellow
            (255, 0, 255),  # Magenta
            (0, 255, 255),  # Cyan
        ]
        
        for i, color in enumerate(colors):
            print(f"Color {i+1}/6: RGB{color}")
            strip.fill(color)
            strip.show()
            time.sleep(1)
        
        # Clear
        print("Clearing LEDs...")
        strip.fill((0, 0, 0))
        strip.show()
        
        strip.deinit()
        return True
        
    except Exception as e:
        print(f"LED test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all component tests"""
    print("OTPi Component Testing")
    print("=" * 30)
    
    # Set environment for testing
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    os.environ.setdefault('OTPI_DEBUG_ENCODER_EVENTS', '1')
    
    results = {}
    
    if len(sys.argv) > 1:
        # Test specific component
        component = sys.argv[1].lower()
        if component == 'encoder':
            results['encoder'] = test_encoder()
        elif component == 'oled':
            results['oled'] = test_oled() 
        elif component == 'leds':
            results['leds'] = test_leds()
        else:
            print(f"Unknown component: {component}")
            print("Available: encoder, oled, leds")
            sys.exit(1)
    else:
        # Test all components
        results['oled'] = test_oled()
        results['encoder'] = test_encoder() 
        results['leds'] = test_leds()
    
    # Summary
    print("\n" + "=" * 30)
    print("Test Results:")
    for component, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  {component.upper()}: {status}")

if __name__ == '__main__':
    main()
