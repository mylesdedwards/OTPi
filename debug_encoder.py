#!/usr/bin/env python3
# debug_encoder.py - Detailed encoder debugging

import os, sys, time
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent))

def debug_encoder_detailed():
    """Debug encoder with detailed pin monitoring"""
    
    print("=== Detailed Encoder Debug ===")
    print("This will show raw pin changes to help debug wiring/connections")
    print()
    
    # Set debug environment
    os.environ['OTPI_DEBUG_ENCODER_EVENTS'] = '1'
    
    try:
        from encoder import Encoder
        
        print("Initializing encoder...")
        enc = Encoder()
        print(f"Backend: {enc.backend_name()}")
        print()
        
        # Show pin configuration
        clk_pin = os.environ.get('OTPI_ENC_CLK', '23')
        dt_pin = os.environ.get('OTPI_ENC_DT', '24') 
        sw_pin = os.environ.get('OTPI_ENC_SW', '25')
        
        print(f"Pin configuration:")
        print(f"  CLK (A): GPIO {clk_pin}")
        print(f"  DT  (B): GPIO {dt_pin}")
        print(f"  SW:      GPIO {sw_pin}")
        print(f"  Button active low: {os.environ.get('OTPI_ENC_BTN_ACTIVE_LOW', '1')}")
        print()
        
        print("Monitoring raw pin levels (rotate encoder slowly):")
        print("Format: CLK DT SW | Steps Pressed")
        print("-" * 40)
        
        last_clk = last_dt = last_sw = None
        step_total = 0
        press_total = 0
        
        for i in range(1000):  # Monitor for ~10 seconds
            try:
                # Get raw levels
                levels = enc.raw_levels()
                if levels:
                    clk, dt, sw = levels
                    
                    # Check for changes
                    changed = False
                    if clk != last_clk or dt != last_dt or sw != last_sw:
                        changed = True
                        last_clk, last_dt, last_sw = clk, dt, sw
                    
                    # Get encoder events
                    steps = enc.steps()
                    pressed = enc.pressed()
                    
                    if steps:
                        step_total += abs(steps)
                    if pressed:
                        press_total += 1
                    
                    # Print if something interesting happened
                    if changed or steps or pressed:
                        status = []
                        if steps:
                            direction = "CW" if steps > 0 else "CCW"
                            status.append(f"Steps: {steps:+2d} ({direction})")
                        if pressed:
                            status.append("PRESSED!")
                        
                        status_str = " | ".join(status) if status else ""
                        print(f" {clk}   {dt}   {sw}  | {status_str}")
                
                else:
                    print("Raw levels not available")
                    break
                    
            except Exception as e:
                print(f"Error reading encoder: {e}")
                break
                
            time.sleep(0.01)  # 100Hz
            
        print("-" * 40)
        print(f"Total: {step_total} steps, {press_total} button presses")
        
        # Wiring check
        print("\nWiring Check:")
        levels = enc.raw_levels()
        if levels:
            clk, dt, sw = levels
            
            print(f"Current levels: CLK={clk}, DT={dt}, SW={sw}")
            
            if clk == dt == sw == 1:
                print("⚠️  All pins reading HIGH - check connections to ground")
            elif clk == dt == sw == 0:
                print("⚠️  All pins reading LOW - check power/pullups")
            else:
                print("✓ Pin levels look reasonable")
                
            if sw == 0:
                print("✓ Button appears to be pressed (if you're holding it)")
            elif sw == 1:
                print("✓ Button appears to be released (if you're not pressing it)")
                
    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as e:
        print(f"Encoder debug failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            enc.close()
        except:
            pass

def test_encoder_config():
    """Test different encoder configurations"""
    
    print("=== Encoder Configuration Test ===")
    
    configs = [
        {"PPR": 2, "POLL_MS": 1},
        {"PPR": 4, "POLL_MS": 1},
        {"PPR": 1, "POLL_MS": 2},
        {"PPR": 4, "POLL_MS": 0.5},
    ]
    
    for config in configs:
        print(f"\nTesting config: {config}")
        
        # Set environment
        os.environ['OTPI_ENC_PPR'] = str(config['PPR'])
        os.environ['OTPI_ENC_POLL_MS'] = str(config['POLL_MS'])
        
        try:
            from encoder import Encoder
            enc = Encoder()
            
            print("Rotate encoder now (5 seconds)...")
            start_time = time.time()
            total_steps = 0
            
            while time.time() - start_time < 5.0:
                steps = enc.steps()
                if steps:
                    total_steps += abs(steps)
                    print(f"  Steps: {steps:+2d}")
                time.sleep(0.01)
            
            print(f"  Total steps detected: {total_steps}")
            enc.close()
            
        except Exception as e:
            print(f"  Failed: {e}")

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'config':
        test_encoder_config()
    else:
        debug_encoder_detailed()
