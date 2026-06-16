import cv2
import os
import numpy as np
import json

class FaceEngine:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.known_faces_dir = os.path.join(data_dir, "known_faces")
        self.db_path = os.path.join(data_dir, "db.json")
        
        # Ensure directories exist
        os.makedirs(self.known_faces_dir, exist_ok=True)
        
        # Load Haar Cascade
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        if self.face_cascade.empty():
            print("Error: Could not load Haar cascade classifier.")
            
        # Initialize LBPH Recognizer
        try:
            self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        except AttributeError:
            print("Warning: cv2.face not available. Fallback to detection-only mode.")
            self.recognizer = None
            
        self.label_to_name = {}
        self.is_trained = False
        self.train_engine()

    def train_engine(self):
        """Train the LBPH recognizer using images in the known_faces directory."""
        if not self.recognizer:
            return False

        # Find all images in the known_faces directory
        valid_extensions = ('.jpg', '.jpeg', '.png')
        image_files = [f for f in os.listdir(self.known_faces_dir) if f.lower().endswith(valid_extensions)]
        
        if not image_files:
            self.is_trained = False
            self.label_to_name = {}
            print("No known faces found. Ready for registering new faces.")
            return False

        faces = []
        labels = []
        name_to_label = {}
        current_label = 0

        # Load database mappings to align names if possible
        for img_file in image_files:
            # File name pattern: Name_Label.jpg or Name.jpg
            # Let's clean the name (remove extension and replace underscores with spaces)
            base_name = os.path.splitext(img_file)[0]
            name = base_name.split('_')[0].replace('-', ' ')
            
            if name not in name_to_label:
                name_to_label[name] = current_label
                self.label_to_name[current_label] = name
                current_label += 1
            
            img_path = os.path.join(self.known_faces_dir, img_file)
            # Read image in grayscale
            gray_img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
            if gray_img is None:
                continue

            # Detect face in the image to make sure we train on cropped faces (using sensitive parameters)
            detected_faces = self.face_cascade.detectMultiScale(gray_img, 1.1, 3)
            if len(detected_faces) > 0:
                for (x, y, w, h) in detected_faces:
                    faces.append(gray_img[y:y+h, x:x+w])
                    labels.append(name_to_label[name])
            else:
                # If cascade detection fails on the training image, resize and use the whole image
                resized = cv2.resize(gray_img, (200, 200))
                faces.append(resized)
                labels.append(name_to_label[name])

        if len(faces) > 0:
            try:
                self.recognizer.train(faces, np.array(labels))
                self.is_trained = True
                print(f"Engine trained successfully with {len(faces)} samples for {len(name_to_label)} users.")
                
                # Update db.json with current known face names
                self._update_db_known_faces(name_to_label.keys())
                return True
            except Exception as e:
                print(f"Error training face recognizer: {e}")
                self.is_trained = False
                return False
        else:
            self.is_trained = False
            return False

    def _update_db_known_faces(self, names_list):
        """Sync registered face names to the db.json file."""
        if not os.path.exists(self.db_path):
            return
        
        try:
            with open(self.db_path, 'r') as f:
                db_data = json.load(f)
            
            db_data['known_faces'] = list(names_list)
            
            with open(self.db_path, 'w') as f:
                json.dump(db_data, f, indent=2)
        except Exception as e:
            print(f"Error updating db.json: {e}")

    def detect_and_recognize(self, frame, threshold=60):
        """
        Detects faces in frame and recognizes them.
        Returns:
            annotated_frame: Frame with drawn bounding boxes and labels.
            detections: List of dictionaries with information on detected faces.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        
        detections = []
        annotated_frame = frame.copy()
        
        for (x, y, w, h) in faces:
            name = "Unknown"
            is_intruder = True
            confidence = 100.0
            color = (0, 0, 255) # Red for Unknown/Intruder
            
            if self.is_trained and self.recognizer:
                face_roi = gray[y:y+h, x:x+w]
                try:
                    label_id, lbph_confidence = self.recognizer.predict(face_roi)
                    
                    # For LBPH, a lower confidence value (distance) means higher match quality.
                    # Calibrate distance to a percentage confidence where:
                    # - 0 distance -> 100%
                    # - 50 distance -> 60%
                    # - 75 distance -> 40%
                    # - 125 distance -> 0%
                    pct_confidence = max(0, min(100, int(100 - (lbph_confidence * 0.8))))
                    
                    if pct_confidence >= threshold and label_id in self.label_to_name:
                        name = self.label_to_name[label_id]
                        is_intruder = False
                        confidence = pct_confidence
                        color = (0, 255, 0) # Green for Known/Authorized
                    else:
                        confidence = pct_confidence
                except Exception as e:
                    print(f"Error predicting face: {e}")
            
            # Draw rectangle around face
            cv2.rectangle(annotated_frame, (x, y), (x+w, y+h), color, 2)
            
            # Label
            label_text = f"{name} ({int(confidence)}%)" if name != "Unknown" else "Intruder (Unknown)"
            cv2.putText(annotated_frame, label_text, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            detections.append({
                "name": name,
                "is_intruder": is_intruder,
                "confidence": confidence,
                "box": (x, y, w, h)
            })
            
        return annotated_frame, detections
