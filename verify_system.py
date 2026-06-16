import sys
import os

# Set path to import from the project directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    print("Testing imports...")
    try:
        import flask
        import cv2
        import numpy as np
        print("  - Flask, OpenCV, and NumPy imported successfully!")
    except ImportError as e:
        print(f"  - Import error: {e}")
        return False
    return True

def test_face_engine():
    print("Testing Face Engine module...")
    try:
        from face_engine import FaceEngine
        engine = FaceEngine(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
        print(f"  - FaceEngine initialized successfully.")
        print(f"  - Haar Cascade loaded: {not engine.face_cascade.empty()}")
        print(f"  - LBPH Face Recognizer available: {engine.recognizer is not None}")
    except Exception as e:
        print(f"  - FaceEngine initialization failed: {e}")
        return False
    return True

def test_flask_routes():
    print("Testing Flask app configuration...")
    try:
        from app import app
        # Create a test client
        client = app.test_client()
        
        # Test dashboard route loads
        response = client.get('/dashboard')
        print(f"  - /dashboard status code: {response.status_code}")
        assert response.status_code == 200, "Dashboard route returned non-200 status code"
        
        # Test database route loads
        response = client.get('/database')
        print(f"  - /database status code: {response.status_code}")
        assert response.status_code == 200, "Database route returned non-200 status code"
        
        # Test settings route loads
        response = client.get('/settings')
        print(f"  - /settings status code: {response.status_code}")
        assert response.status_code == 200, "Settings route returned non-200 status code"
        
        # Test logs route loads
        response = client.get('/logs')
        print(f"  - /logs status code: {response.status_code}")
        assert response.status_code == 200, "Logs route returned non-200 status code"

        # Test messages route loads
        response = client.get('/messages')
        print(f"  - /messages status code: {response.status_code}")
        assert response.status_code == 200, "Messages route returned non-200 status code"
        
        print("  - All Flask routes verified successfully!")
    except Exception as e:
        print(f"  - Flask app verification failed: {e}")
        return False
    return True

if __name__ == "__main__":
    print("=== STARTING SECURITY DETECTOR SYSTEM VERIFICATION ===\n")
    success = test_imports() and test_face_engine() and test_flask_routes()
    print("\n=======================================================")
    if success:
        print("VERIFICATION SUCCESSFUL: The system is ready to be run!")
        sys.exit(0)
    else:
        print("VERIFICATION FAILED: Please check the errors above.")
        sys.exit(1)
