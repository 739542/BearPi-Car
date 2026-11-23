"""Click the same front and rear landmarks in several COLMAP source images."""

import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageOps, ImageTk


def to_image_xy(x, y, original_size, display_size):
    width, height = original_size
    display_width, display_height = display_size
    return [(x + 0.5) * width / display_width, (y + 0.5) * height / display_height]


def load_labels(path, photos):
    if path is None:
        return {}, {}
    saved = json.loads(path.read_text(encoding="utf-8"))["images"]
    labels = {}
    sizes = {}
    for photo in photos:
        if photo.name not in saved:
            continue
        item = saved[photo.name]
        with Image.open(photo) as source:
            width, height = ImageOps.exif_transpose(source).size
        if item["image_size"] != [width, height]:
            raise ValueError(f"Saved image size does not match {photo.name}")
        points = [item["front_xy"], item["rear_xy"]]
        if any(not (0 <= x < width and 0 <= y < height) for x, y in points):
            raise ValueError(f"Saved landmark outside the image: {photo.name}")
        labels[photo.name] = points
        sizes[photo.name] = (width, height)
    if not labels:
        raise ValueError("No saved annotations match these image names")
    return labels, sizes


class Annotator:
    def __init__(self, window, photos, output, labels=None, sizes=None):
        self.window = window
        self.photos = photos
        self.output = output
        self.index = 0
        self.labels = labels if labels is not None else {}
        self.sizes = sizes if sizes is not None else {}
        self.replacing_front = False

        window.title("小车方向标注")
        self.status = tk.Label(window, padx=12, pady=8)
        self.status.pack()
        self.canvas = tk.Canvas(window, highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.click)

        buttons = tk.Frame(window, padx=10, pady=8)
        buttons.pack(fill="x")
        tk.Button(buttons, text="上一张", command=lambda: self.move(-1)).pack(side="left", padx=3)
        tk.Button(buttons, text="撤销最后一点", command=self.undo).pack(side="left", padx=3)
        tk.Button(buttons, text="重标车头", command=self.redo_front).pack(side="left", padx=3)
        tk.Button(buttons, text="下一张", command=lambda: self.move(1)).pack(side="left", padx=3)
        tk.Button(buttons, text="保存标注", command=self.save).pack(side="right", padx=3)
        self.show()

    def show(self):
        self.replacing_front = False
        path = self.photos[self.index]
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        self.sizes[path.name] = image.size
        image.thumbnail((self.window.winfo_screenwidth() - 90, self.window.winfo_screenheight() - 150))
        self.display_size = image.size
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.configure(width=image.width, height=image.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.redraw_points()

    def redraw_points(self):
        name = self.photos[self.index].name
        points = self.labels.get(name, [])
        self.canvas.delete("landmark")
        width, height = self.sizes[name]
        draw_width, draw_height = self.display_size
        for (x, y), label, color in zip(points, ("车头", "车尾"), ("#38e27b", "#ffab48")):
            u = x * draw_width / width
            v = y * draw_height / height
            self.canvas.create_oval(u - 5, v - 5, u + 5, v + 5, fill=color, outline="black", tags="landmark")
            self.canvas.create_text(u + 9, v - 9, anchor="sw", text=label, fill=color, tags="landmark")
        instruction = ("请点车头的参考点", "请点车尾的参考点", "本图已标完，可以切到下一张")[len(points)]
        if self.replacing_front:
            instruction = "请点新的车头参考点"
        self.status.configure(text=f"{self.index + 1}/{len(self.photos)}   {name}   {instruction}")

    def click(self, event):
        name = self.photos[self.index].name
        points = self.labels.setdefault(name, [])
        if len(points) >= 2 and not self.replacing_front:
            return
        xy = to_image_xy(event.x, event.y, self.sizes[name], self.display_size)
        if self.replacing_front:
            points[0] = xy
            self.replacing_front = False
        else:
            points.append(xy)
        self.redraw_points()

    def redo_front(self):
        if self.labels.get(self.photos[self.index].name):
            self.replacing_front = True
            self.redraw_points()

    def undo(self):
        self.replacing_front = False
        points = self.labels.get(self.photos[self.index].name)
        if points:
            points.pop()
            self.redraw_points()

    def move(self, step):
        destination = self.index + step
        if 0 <= destination < len(self.photos):
            self.index = destination
            self.show()

    def save(self):
        images = {
            name: {"image_size": self.sizes[name], "front_xy": points[0], "rear_xy": points[1]}
            for name, points in self.labels.items()
            if len(points) == 2
        }
        if len(images) < 2:
            messagebox.showinfo("标注未完成", "请在至少两张照片里点完车头和车尾。")
            return
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            with self.output.open("x", encoding="utf-8") as stream:
                json.dump({"coordinate_system": "original image pixels, top-left origin", "images": images},
                          stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        print(f"Saved {len(images)} views to {self.output.resolve()}")
        messagebox.showinfo("标注已保存", str(self.output.resolve()))
        self.window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Path to a new JSON file")
    parser.add_argument("--resume", type=Path, help="Previously saved annotation JSON")
    args = parser.parse_args()

    photos = sorted(path for path in args.image_dir.glob("*")
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if len(photos) < 2:
        parser.error("Image directory must contain at least two JPG or PNG photos")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    try:
        labels, sizes = load_labels(args.resume, photos)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(f"Could not load annotations: {exc}")

    window = tk.Tk()
    Annotator(window, photos, args.output, labels, sizes)
    window.mainloop()


if __name__ == "__main__":
    main()
