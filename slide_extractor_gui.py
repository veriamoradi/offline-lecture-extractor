import os
import sys
import time
import queue
import threading
import traceback
import shutil

# --- DEPENDENCY PRE-CHECK [ERR_DEP_701] ---
missing_packages = []
try:
    import customtkinter as ctk
except ImportError:
    missing_packages.append("customtkinter")
try:
    import cv2
except ImportError:
    missing_packages.append("opencv-python")
try:
    import fitz  # PyMuPDF
except ImportError:
    missing_packages.append("pymupdf")
try:
    from skimage.metrics import structural_similarity as ssim
except ImportError:
    missing_packages.append("scikit-image")
try:
    import numpy as np
except ImportError:
    missing_packages.append("numpy")

# If any dependencies are missing, show a native alert and terminate safely
if missing_packages:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    
    # Map internal imports to their corresponding pip install names
    pip_names = {
        "customtkinter": "customtkinter",
        "opencv-python": "opencv-python",
        "pymupdf": "pymupdf",
        "scikit-image": "scikit-image",
        "numpy": "numpy"
    }
    needed_install = [pip_names.get(p, p) for p in missing_packages]
    install_cmd = f"pip install {' '.join(needed_install)}"
    
    messagebox.showerror(
        "Dependency Error [ERR_DEP_701]",
        f"Error Code: ERR_DEP_701\n\n"
        f"The following required Python packages are missing:\n"
        f"{', '.join(missing_packages)}\n\n"
        f"Please open your Terminal / CMD and run the following command to install them:\n\n"
        f"{install_cmd}"
    )
    sys.exit(1)


# --- PATH SANITIZATION & UNICODE I/O UTILITIES ---
def sanitize_path(path):
    """
    Normalizes file paths, resolving issues with long Windows paths (>260 characters)
    and formatting extended-length prefixes (converting forward-slashes & UNC paths).
    """
    if not path:
        return ""
    
    if sys.platform.startswith('win'):
        path_str = path.replace('/', '\\')
        
        if path_str.startswith('\\\\?\\') or path_str.startswith('//?/'):
            raw_path = path_str[4:]
            abs_raw = os.path.abspath(raw_path)
            if len(abs_raw) < 260:
                return abs_raw
            else:
                return '\\\\?\\' + abs_raw
        
        abs_path = os.path.abspath(path_str)
        if len(abs_path) >= 260:
            return '\\\\?\\' + abs_path
            
        return abs_path
    else:
        return os.path.abspath(os.path.normpath(path))

def safe_cv2_imwrite(filename, img):
    """
    Safe image writer that bypasses OpenCV's inability to handle non-ASCII 
    (Unicode/Persian/Arabic) characters in file paths on Windows.
    """
    try:
        is_success, im_buf_arr = cv2.imencode(".jpg", img)
        if is_success:
            im_buf_arr.tofile(filename)
            return True
        return False
    except Exception:
        return False


# --- MAIN CUSTOMTKINTER APPLICATION ---
class SlideExtractorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window configuration with spacious defaults
        self.title("Smart Slide Extractor & PDF Creator")
        self.geometry("720x660")
        self.minsize(680, 600)
        
        # Configure overall themes (Supports automatic Dark/Light system modes)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        
        # Queue for thread-safe asynchronous operations
        self.queue = queue.Queue()
        
        # Process controls
        self.is_processing = False
        self.stop_requested = False
        self.worker_thread = None
        
        # UI State Variables
        self.video_path_var = ctk.StringVar()
        self.pdf_path_var = ctk.StringVar()
        
        # Dynamic Algorithm State (SSIM or Pixel Difference)
        self.algo_var = ctk.StringVar(value="SSIM (Structural Similarity)")
        
        # Variables that change limits dynamically based on selected algorithm
        self.threshold_var = ctk.DoubleVar(value=95.0)  # Starts with SSIM default (95.0%)
        self.skip_seconds_var = ctk.IntVar(value=1)     # Default check interval is 1 second
        self.progress_var = ctk.DoubleVar(value=0.0)
        self.status_var = ctk.StringVar(value="Ready. Please select a video file.")
        self.stats_var = ctk.StringVar(value="Extracted Slides: 0")
        
        self.create_widgets()
        
        # Start scanning the message queue
        self.after(100, self.process_queue)

    def create_widgets(self):
        # Apply padding to the main window grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # --- HEADER ---
        header_label = ctk.CTkLabel(
            self, 
            text="Offline Lecture Slide Extractor & PDF Creator", 
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        )
        header_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        # Main content container
        container = ctk.CTkFrame(self, corner_radius=15)
        container.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        
        # Bind resize event to the main container to dynamically adjust wrapping (Issue C)
        container.bind("<Configure>", self.on_container_resize)
        
        # --- SECTION 1: Files Card ---
        files_frame = ctk.CTkFrame(container, fg_color="transparent")
        files_frame.grid(row=0, column=0, padx=20, pady=15, sticky="ew")
        files_frame.grid_columnconfigure(1, weight=1)
        
        # Header for files section
        sec1_title = ctk.CTkLabel(files_frame, text="File Selection", font=ctk.CTkFont(size=13, weight="bold"))
        sec1_title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        
        # Video Selection Elements
        video_label = ctk.CTkLabel(files_frame, text="Input Video:")
        video_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=8)
        video_entry = ctk.CTkEntry(files_frame, textvariable=self.video_path_var, placeholder_text="Select your lecture video...")
        video_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=8)
        video_btn = ctk.CTkButton(files_frame, text="Browse...", width=100, command=self.browse_video)
        video_btn.grid(row=1, column=2, padx=(5, 0), pady=8)
        
        # PDF Selection Elements
        pdf_label = ctk.CTkLabel(files_frame, text="Save PDF To:")
        pdf_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=8)
        pdf_entry = ctk.CTkEntry(files_frame, textvariable=self.pdf_path_var, placeholder_text="Choose destination path...")
        pdf_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=8)
        pdf_btn = ctk.CTkButton(files_frame, text="Browse...", width=100, command=self.browse_pdf)
        pdf_btn.grid(row=2, column=2, padx=(5, 0), pady=8)
        
        # --- SECTION 2: Algorithm Parameters & Selection ---
        settings_frame = ctk.CTkFrame(container)
        settings_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        settings_frame.grid_columnconfigure(1, weight=1)
        
        sec2_title = ctk.CTkLabel(settings_frame, text="Processor Algorithm & Sensitivity Settings", font=ctk.CTkFont(size=13, weight="bold"))
        sec2_title.grid(row=0, column=0, columnspan=3, sticky="w", padx=15, pady=(10, 5))
        
        # Algorithm Selector Dropdown Menu
        algo_label = ctk.CTkLabel(settings_frame, text="Detection Algorithm:")
        algo_label.grid(row=1, column=0, sticky="w", padx=15, pady=10)
        
        self.algo_menu = ctk.CTkOptionMenu(
            settings_frame,
            values=["SSIM (Structural Similarity)", "Pixel Difference (Absolute Diff)"],
            variable=self.algo_var,
            command=self.on_algorithm_change
        )
        self.algo_menu.grid(row=1, column=1, columnspan=2, sticky="w", padx=10, pady=10)
        
        # Dynamic Sensitivity Slider
        self.thresh_label = ctk.CTkLabel(settings_frame, text="SSIM Similarity Threshold:")
        self.thresh_label.grid(row=2, column=0, sticky="w", padx=15, pady=10)
        
        self.thresh_slider = ctk.CTkSlider(settings_frame, from_=80.0, to=99.9, variable=self.threshold_var)
        self.thresh_slider.grid(row=2, column=1, sticky="ew", padx=10, pady=10)
        
        self.thresh_val_label = ctk.CTkLabel(settings_frame, text=f"{self.threshold_var.get():.1f} %", width=60)
        self.thresh_val_label.grid(row=2, column=2, padx=(5, 15), pady=10)
        self.threshold_var.trace_add("write", lambda *args: self.thresh_val_label.configure(text=f"{self.threshold_var.get():.1f} %"))
        
        # Scan Interval Input Slider
        skip_label = ctk.CTkLabel(settings_frame, text="Check Interval (Seconds):")
        skip_label.grid(row=3, column=0, sticky="w", padx=15, pady=10)
        
        skip_slider = ctk.CTkSlider(settings_frame, from_=1, to=10, number_of_steps=9, variable=self.skip_seconds_var)
        skip_slider.grid(row=3, column=1, sticky="ew", padx=10, pady=10)
        
        self.skip_val_label = ctk.CTkLabel(settings_frame, text=f"{self.skip_seconds_var.get()} sec", width=60)
        self.skip_val_label.grid(row=3, column=2, padx=(5, 15), pady=10)
        self.skip_seconds_var.trace_add("write", lambda *args: self.skip_val_label.configure(text=f"{self.skip_seconds_var.get()} sec"))
        
        # Dynamic Tooltip Help Card - Set larger pady=(10, 25) to fix bottom collision (Issue A)
        self.help_label = ctk.CTkLabel(
            settings_frame, 
            text="💡 Tip: SSIM compares structural layout. 95% is optimal. Raise to 98% for minor details, lower to 90% to ignore camera shake.", 
            font=ctk.CTkFont(size=11), 
            text_color="#64748b",
            justify="left"
        )
        self.help_label.grid(row=4, column=0, columnspan=3, padx=15, pady=(10, 25), sticky="w")
        
        # --- SECTION 3: Control Buttons ---
        control_frame = ctk.CTkFrame(self, fg_color="transparent")
        control_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")
        control_frame.grid_columnconfigure(0, weight=4)
        control_frame.grid_columnconfigure(1, weight=1)
        
        self.start_btn = ctk.CTkButton(
            control_frame, 
            text="🚀 Start Extracting Slides", 
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            command=self.start_processing
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        self.stop_btn = ctk.CTkButton(
            control_frame, 
            text="🛑 Stop Process", 
            height=40,
            fg_color="#ef4444",
            hover_color="#dc2626",
            state="disabled",
            command=self.stop_processing
        )
        self.stop_btn.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        
        # --- SECTION 4: Progress Panel ---
        progress_frame = ctk.CTkFrame(self, corner_radius=10)
        progress_frame.grid(row=3, column=0, padx=20, pady=(10, 20), sticky="ew")
        progress_frame.grid_columnconfigure(0, weight=1)
        
        self.status_label = ctk.CTkLabel(progress_frame, textvariable=self.status_var, font=ctk.CTkFont(size=12, weight="bold"), anchor="w")
        self.status_label.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")
        
        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.grid(row=1, column=0, padx=15, pady=5, sticky="ew")
        self.progress_bar.set(0.0)
        
        self.stats_label = ctk.CTkLabel(progress_frame, textvariable=self.stats_var, font=ctk.CTkFont(size=11), text_color="#64748b", anchor="w")
        self.stats_label.grid(row=2, column=0, padx=15, pady=(2, 10), sticky="w")

    def on_container_resize(self, event):
        """Dynamically adjusts help label's wraplength on window/container resizing to resolve high-DPI scaling issues (Issue C)"""
        container_width = event.width
        # Subtract absolute outer boundaries and internal paddings to get precise available width
        usable_width = container_width - 80
        if usable_width > 100:
            self.help_label.configure(wraplength=usable_width)

    def on_algorithm_change(self, choice):
        """Dynamically adjusts UI parameter limits and guidelines depending on selected algorithm"""
        if choice == "SSIM (Structural Similarity)":
            self.thresh_label.configure(text="SSIM Similarity Threshold:")
            self.thresh_slider.configure(from_=80.0, to=99.9)
            self.threshold_var.set(95.0)
            self.help_label.configure(
                text="💡 Tip: SSIM compares structural layout. 95% is optimal. Raise to 98% for minor details, lower to 90% to ignore camera shake."
            )
        else:
            self.thresh_label.configure(text="Pixel Change Threshold:")
            self.thresh_slider.configure(from_=0.5, to=15.0)
            self.threshold_var.set(3.0)
            self.help_label.configure(
                text="💡 Tip: Pixel Difference compares raw pixel changes. 3.0% is optimal. Lower it to be more sensitive to small changes, raise it to ignore mouse movements."
            )

    def browse_video(self):
        from tkinter import filedialog
        file_path = filedialog.askopenfilename(
            title="Select Lecture Video File",
            filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov *.flv *.wmv"), ("All files", "*.*")]
        )
        if file_path:
            sanitized = sanitize_path(file_path)
            self.video_path_var.set(sanitized)
            base_path, _ = os.path.splitext(sanitized)
            self.pdf_path_var.set(base_path + "_slides.pdf")

    def browse_pdf(self):
        from tkinter import filedialog
        file_path = filedialog.asksaveasfilename(
            title="Select Path to Save PDF File",
            defaultextension=".pdf",
            filetypes=[("PDF Document", "*.pdf")]
        )
        if file_path:
            self.pdf_path_var.set(sanitize_path(file_path))

    def set_ui_state(self, processing):
        self.is_processing = processing
        if processing:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.algo_menu.configure(state="disabled")
        else:
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.algo_menu.configure(state="normal")

    def start_processing(self):
        from tkinter import messagebox
        video_path = sanitize_path(self.video_path_var.get())
        pdf_path = sanitize_path(self.pdf_path_var.get())
        
        self.video_path_var.set(video_path)
        self.pdf_path_var.set(pdf_path)
        
        # Validation checks
        if not video_path or not os.path.exists(video_path):
            messagebox.showerror(
                "Validation Error [ERR_VAL_101]", 
                "Error Code: ERR_VAL_101\n\nPlease select a valid input video file first. The specified file does not exist."
            )
            return
            
        if not pdf_path:
            messagebox.showerror(
                "Validation Error [ERR_VAL_102]", 
                "Error Code: ERR_VAL_102\n\nPlease specify the destination path for the output PDF file."
            )
            return
            
        self.set_ui_state(processing=True)
        self.stop_requested = False
        self.progress_bar.set(0.0)
        
        # Save threshold configuration and selection type
        threshold = self.threshold_var.get()
        skip_sec = self.skip_seconds_var.get()
        algo_choice = self.algo_var.get()
        
        # Run background video extractor thread
        self.worker_thread = threading.Thread(
            target=self.process_video_thread,
            args=(video_path, pdf_path, algo_choice, threshold, skip_sec),
            daemon=True
        )
        self.worker_thread.start()

    def stop_processing(self):
        from tkinter import messagebox
        if messagebox.askyesno("Confirm Cancel", "Are you sure you want to stop processing the video?"):
            self.stop_requested = True
            self.status_var.set("Requesting stop. Cleaning up...")

    def process_video_thread(self, video_path, pdf_path, algo_choice, threshold, skip_seconds):
        video_path = sanitize_path(video_path)
        pdf_path = sanitize_path(pdf_path)
        
        # [ERR_CV_201] OpenCV initialization check
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.queue.put(("error", ("ERR_CV_201", "Unable to open the selected video file. OpenCV decoder failed.")))
            return

        # [ERR_CV_202] Metadata check
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps == 0 or total_frames == 0:
            self.queue.put(("error", ("ERR_CV_202", "Invalid video format or unable to read frame metadata (FPS/Framecount is zero).")))
            cap.release()
            return

        duration_sec = total_frames / fps
        
        # Create a unique temporary directory to avoid collisions if app crashed previously or runs multiple times
        unique_id = int(time.time())
        temp_folder = sanitize_path(os.path.abspath(f"temp_extracted_frames_{unique_id}"))
        
        # [ERR_IO_301] Temp directory creation check
        try:
            os.makedirs(temp_folder, exist_ok=True)
        except Exception as e:
            self.queue.put(("error", ("ERR_IO_301", f"Failed to create temporary folder. Message: {str(e)}")))
            cap.release()
            return

        saved_image_paths = []
        last_saved_gray = None
        frame_index = 0
        saved_count = 0
        
        # Calculate step interval in frames
        frame_skip_step = int(fps * skip_seconds)
        if frame_skip_step < 1:
            frame_skip_step = 1

        # Calculate formatted total duration strings
        total_hours = int(duration_sec // 3600)
        total_minutes = int((duration_sec % 3600) // 60)
        total_seconds = int(duration_sec % 60)
        total_time_str = f"{total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}"

        # [ERR_PROC_401] Main Processing exception frame
        try:
            while cap.isOpened():
                if self.stop_requested:
                    break
                    
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ret, frame = cap.read()
                if not ret:
                    break

                # Frame pre-processing
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
                # Apply dynamic gaussian blurring kernel based on chosen algorithm
                if algo_choice == "SSIM (Structural Similarity)":
                    gray_blurred = cv2.GaussianBlur(gray, (5, 5), 0)
                else:
                    # Pixel difference works best with higher blurring to wipe out digital compression artifacts
                    gray_blurred = cv2.GaussianBlur(gray, (21, 21), 0)

                # Current time formatting (HH:MM:SS)
                current_sec = int(frame_index / fps)
                current_hours = current_sec // 3600
                current_minutes = (current_sec % 3600) // 60
                current_seconds = current_sec % 60
                
                percent_done = (frame_index / total_frames) * 100
                time_str = f"{current_hours:02d}:{current_minutes:02d}:{current_seconds:02d} of {total_time_str}"
                
                self.queue.put(("progress", (percent_done, time_str, saved_count)))

                if last_saved_gray is not None:
                    # Guarantee shape compatibility
                    if last_saved_gray.shape != gray_blurred.shape:
                        gray_blurred = cv2.resize(gray_blurred, (last_saved_gray.shape[1], last_saved_gray.shape[0]))
                    
                    is_transition = False
                    
                    # Algorithm branching based on user's dynamic GUI selection
                    if algo_choice == "SSIM (Structural Similarity)":
                        ssim_threshold = threshold / 100.0  # e.g., 95.0% -> 0.95
                        similarity_score = ssim(last_saved_gray, gray_blurred)
                        if similarity_score < ssim_threshold:
                            is_transition = True
                    else:
                        # Pixel difference mode (Absolute difference and thresholding)
                        pixel_change_threshold = threshold / 100.0  # e.g., 3.0% -> 0.03
                        frame_diff = cv2.absdiff(last_saved_gray, gray_blurred)
                        _, thresh_img = cv2.threshold(frame_diff, 25, 255, cv2.THRESH_BINARY)
                        
                        changed_pixels = cv2.countNonZero(thresh_img)
                        total_pixels = gray.shape[0] * gray.shape[1]
                        change_ratio = changed_pixels / total_pixels
                        if change_ratio > pixel_change_threshold:
                            is_transition = True
                    
                    # Capture and save frame if difference threshold condition was met
                    if is_transition:
                        img_path = sanitize_path(os.path.join(temp_folder, f"slide_{saved_count:04d}_{current_hours:02d}{current_minutes:02d}{current_seconds:02d}.jpg"))
                        if safe_cv2_imwrite(img_path, frame):
                            saved_image_paths.append(img_path)
                            saved_count += 1
                            last_saved_gray = gray_blurred  # Update current benchmark reference
                else:
                    # Save index-zero initial frame as starting baseline slide 
                    img_path = sanitize_path(os.path.join(temp_folder, f"slide_{saved_count:04d}_000000.jpg"))
                    if safe_cv2_imwrite(img_path, frame):
                        saved_image_paths.append(img_path)
                        saved_count += 1
                        last_saved_gray = gray_blurred

                frame_index += frame_skip_step
                if frame_index >= total_frames:
                    break
        except Exception as e:
            self.queue.put(("error", ("ERR_PROC_401", f"Unexpected error during frame processing. Traceback: {traceback.format_exc()}")))
            cap.release()
            return

        cap.release()

        # Handle user abort cleanup (using shutil for safe recursive deletion)
        if self.stop_requested:
            if os.path.exists(temp_folder):
                shutil.rmtree(temp_folder, ignore_errors=True)
            self.queue.put(("cancelled", "Processing was cancelled by the user."))
            return

        # [ERR_EMPTY_501] Empty frame list check
        if not saved_image_paths:
            if os.path.exists(temp_folder):
                shutil.rmtree(temp_folder, ignore_errors=True)
            self.queue.put(("error", ("ERR_EMPTY_501", "No slides were extracted. Your threshold might be set too high, or the video is static.")))
            return

        # [ERR_PDF_601] Compile images to single PDF using PyMuPDF (fitz)
        self.queue.put(("status", "Compiling images to high-quality compressed PDF via PyMuPDF..."))
        try:
            pdf_doc = fitz.open()
            for img_path in saved_image_paths:
                img = fitz.open(img_path)
                pdf_bytes = img.convert_to_pdf()
                img.close()
                img_pdf = fitz.open("pdf", pdf_bytes)
                pdf_doc.insert_pdf(img_pdf)
                img_pdf.close()
                
            pdf_doc.save(pdf_path)
            pdf_doc.close()
        except Exception as e:
            self.queue.put(("error", ("ERR_PDF_601", f"Failed to compile PDF document with PyMuPDF. Message: {str(e)}")))
            return

        # [ERR_IO_302] Workspace cleanup execution
        try:
            if os.path.exists(temp_folder):
                shutil.rmtree(temp_folder, ignore_errors=True)
        except Exception as e:
            print(f"[Warning ERR_IO_302] Temp cleanup warning: {str(e)}")
            
        self.queue.put(("success", f"PDF successfully created and saved at:\n{pdf_path}"))

    def process_queue(self):
        """Monitors and processes queue messages safely inside the main UI loop"""
        from tkinter import messagebox
        try:
            while True:
                msg_type, data = self.queue.get_nowait()
                
                if msg_type == "progress":
                    percent, time_str, saved_count = data
                    self.progress_bar.set(percent / 100.0)  # CustomTkinter progress mapping (0.0 to 1.0)
                    self.status_var.set(f"Analyzing video... ({time_str})")
                    self.stats_var.set(f"Slides extracted so far: {saved_count}")
                    
                elif msg_type == "status":
                    self.status_var.set(data)
                    
                elif msg_type == "success":
                    self.progress_bar.set(1.0)
                    self.status_var.set("Process completed successfully!")
                    self.set_ui_state(processing=False)
                    messagebox.showinfo("Success", data)
                    
                elif msg_type == "error":
                    err_code, err_msg = data
                    self.status_var.set(f"An error occurred ({err_code}).")
                    self.set_ui_state(processing=False)
                    messagebox.showerror(
                        f"System Error [{err_code}]", 
                        f"Error Code: {err_code}\n\nDescription:\n{err_msg}\n\n"
                        f"Please ensure configurations are correct or files are accessible."
                    )
                    
                elif msg_type == "cancelled":
                    self.progress_bar.set(0.0)
                    self.status_var.set("Process stopped.")
                    self.stats_var.set("Slides extracted: 0")
                    self.set_ui_state(processing=False)
                    messagebox.showwarning("Process Stopped", data)
                    
                self.queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_queue)


if __name__ == "__main__":
    app = SlideExtractorApp()
    app.mainloop()