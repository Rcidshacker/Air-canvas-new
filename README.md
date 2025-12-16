````markdown
# 🎨 Air Canvas with Depth-Anything V2

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-green?style=for-the-badge&logo=opencv&logoColor=white)](https://opencv.org/)
[![MediaPipe](https://img.shields.io/badge/MediaPipe-Hand%20Tracking-orange?style=for-the-badge&logo=google&logoColor=white)](https://developers.google.com/mediapipe)
[![PyTorch](https://img.shields.io/badge/PyTorch-Deep%20Learning-red?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)

**Draw in the air using hand gestures, empowered by AI depth estimation.**

[View Demo](#-demo) • [Installation](#-installation) • [Controls](#-controls--shortcuts) • [Configuration](#-configuration)

</div>

---

## 📖 Overview

**Air Canvas** transforms your webcam into a touch-free drawing tablet. By combining **MediaPipe** for precise hand tracking and **Depth-Anything V2** for intelligent depth estimation, this application distinguishes between your hand moving *over* the canvas versus *touching* it.

It's not just a toy—it's a fully functional drawing tool with shape primitives, saving capabilities, and even Optical Character Recognition (OCR) to convert your air-writing into text!

## ✨ Key Features

- **✋ AI Hand Tracking**: Real-time, low-latency tracking of fingertips using MediaPipe.
- **🧠 Depth-Aware Interaction**: Uses **Depth-Anything V2** to detect "touch" depth, allowing you to hover without drawing.
- **🖌️ Creative Tools**:
  - **Free Draw**: Sketch naturally with smooth spline interpolation.
  - **Shapes**: Instant **Lines**, **Rectangles**, and **Circles**.
  - **Eraser**: Intuitive erasing tool.
- **🎨 Dynamic Palette**: Change colors and brush sizes with virtual buttons.
- **📷 Smart Features**:
  - **OCR**: Convert drawn text to digital string on the fly.
  - **Screenshots**: Save your masterpiece with a single keystroke.
  - **Image Processing**: Real-time brightness and contrast adjustment.

---

## 🚀 Installation

### 1. Clone the Repository
```bash
git clone [https://github.com/Rcidshacker/Air-canvas-new.git](https://github.com/Rcidshacker/Air-canvas-new.git)
cd Air-canvas-new
````

### 2\. Set Up Virtual Environment

It is highly recommended to use a virtual environment to manage dependencies.

\<details\>
\<summary\>\<strong\>Click to show instructions for Windows / Mac / Linux\</strong\>\</summary\>

#### Windows

```bash
python -m venv .depthv2
.\.depthv2\Scripts\activate
```

#### Mac / Linux

```bash
python3 -m venv .depthv2
source .depthv2/bin/activate
```

\</details\>

### 3\. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4\. Install Tesseract OCR (Required for Text Recognition)

This project uses Tesseract for OCR features.

  - **Windows**: Download the installer from [UB-Mannheim/tesseract](https://www.google.com/search?q=https://github.com/UB-Mannheim/tesseract/wiki).
      - *Note: Ensure the path in `depthtrack.py` matches your installation (Default: `C:\Program Files\Tesseract-OCR\tesseract.exe`).*
  - **Linux**: `sudo apt-get install tesseract-ocr`
  - **Mac**: `brew install tesseract`

### 5\. Download Model Weights ⚠️

**Crucial Step**: You must manually download the depth model weights as they are too large for GitHub.

1.  Download **`depth_anything_v2_vits.pth`** from the [Depth-Anything V2 Repository](https://github.com/DepthAnything/Depth-Anything-V2).
2.  Create a folder named `model_weights` in the project root.
3.  Place the `.pth` file inside `model_weights/`.

-----

## 🎮 Controls & Shortcuts

### Hand Gestures

| Gesture | Action |
| :--- | :--- |
| **Index Finger Point** | Move Cursor (Hover) |
| **Pinch (Index + Thumb)** | **Draw / Select Tool** (Simulates Mouse Click) |

### Keyboard Shortcuts

| Key | Function |
| :---: | :--- |
| **`r`** | **Reset/Clear** the Canvas |
| **`s`** | **Save Screenshot** (Saved to project folder) |
| **`o`** | **OCR**: Read text from canvas and print to console |
| **`u`** | **Update Config**: Reload `config.yaml` without restarting |
| **`b`** / **`v`** | Increase / Decrease **Brightness** |
| **`c`** / **`x`** | Increase / Decrease **Contrast** |
| **`ESC`** | **Quit** Application |

-----

## ⚙️ Configuration

You can customize the application behavior by editing `config.yaml`. No coding required\!

```yaml
# Example settings in config.yaml
window_width: 1280       # Canvas width
use_depth: true          # Toggle AI depth estimation
brush_sizes: [5, 10, 15] # Available brush sizes
colors:                  # RGB Color palette
  - [255, 255, 255]      # White
  - [0, 0, 0]            # Black
  - [255, 0, 0]          # Red
```

-----

## ▶️ How to Run

Ensure your webcam is connected and you have activated your virtual environment.

```bash
python depthtrack.py
```

-----

## 📂 Project Structure

```text
📦 Air-canvas-new
 ┣ 📂 model_weights       # ⚠️ Place depth_anything_v2_vits.pth here
 ┣ 📂 utils               # Utility scripts
 ┣ 📜 depthtrack.py       # Main Application Entry Point
 ┣ 📜 config.yaml         # User Configuration
 ┣ 📜 requirements.txt    # Python Dependencies
 ┣ 🖼️ circle.png          # UI Asset
 ┣ 🖼️ cursor.png          # UI Asset
 ┣ 🖼️ draw.png            # UI Asset
 ┗ 📜 README.md           # Documentation
```

-----

## 🤝 Contributing

Contributions are welcome\! Feel free to open issues or submit pull requests.

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

-----

## 👨‍💻 Author

**Ruchit** [LinkedIn](https://www.linkedin.com/in/ruchit-das-3b6a8a252/) | [GitHub](https://github.com/Rcidshacker)

-----

\<div align="center"\>
Made with ❤️ and Python
\</div\>

```
```
