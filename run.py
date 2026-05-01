import subprocess
import sys
import os
import platform
import time

def main():
    print("===================================================")
    print("    Starting Boolean Trader (Single Terminal)")
    print("===================================================")

    is_windows = platform.system() == "Windows"
    
    # Locate python executable in venv
    if is_windows:
        python_exec = os.path.join("venv", "Scripts", "python.exe")
    else:
        python_exec = os.path.join("venv", "bin", "python")
        
    # Fallback if venv is not present
    if not os.path.exists(python_exec):
        print("[WARNING] Virtual environment not found at 'venv'. Falling back to global python.")
        python_exec = sys.executable

    try:
        # Start Backend
        print("[INFO] Launching Backend...")
        backend_cmd = [python_exec, "-m", "uvicorn", "api.main:app", "--reload"]
        backend_process = subprocess.Popen(backend_cmd)

        # Give backend a moment to start
        time.sleep(1)

        # Start Frontend
        print("[INFO] Launching Frontend...")
        npm_cmd = "npm.cmd" if is_windows else "npm"
        frontend_cmd = [npm_cmd, "run", "dev"]
        frontend_process = subprocess.Popen(frontend_cmd, cwd="frontend")

        # Wait for both processes
        backend_process.wait()
        frontend_process.wait()

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down processes...")
        backend_process.terminate()
        frontend_process.terminate()
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] An error occurred: {e}")
        try:
            backend_process.terminate()
            frontend_process.terminate()
        except:
            pass

if __name__ == "__main__":
    main()
