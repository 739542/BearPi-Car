"""Detect named objects and export one mask per instance from a local image."""

import argparse
import json
from pathlib import Path

DETECTOR = "IDEA-Research/grounding-dino-tiny"
SEGMENTER = "facebook/sam2.1-hiera-tiny"
COLORS = [(40, 170, 255), (255, 95, 105), (60, 200, 120), (240, 170, 45)]


def segment(image, prompt, device, box_threshold, text_threshold):
    import torch
    from transformers import (
        AutoModelForZeroShotObjectDetection,
        AutoProcessor,
        Sam2Model,
        Sam2Processor,
    )

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this PyTorch environment")

    print(f"Detecting on {device} with {DETECTOR}", flush=True)
    processor = AutoProcessor.from_pretrained(DETECTOR)
    detector = AutoModelForZeroShotObjectDetection.from_pretrained(
        DETECTOR, use_safetensors=True, disable_custom_kernels=True
    ).to(device).eval()
    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device)
    with torch.inference_mode():
        predictions = detector(**inputs)
        detections = processor.post_process_grounded_object_detection(
            predictions,
            inputs.input_ids,
            threshold=box_threshold,
            text_threshold=text_threshold,
            target_sizes=[(image.height, image.width)],
        )[0]

    boxes = detections["boxes"].cpu().tolist()
    if not boxes:
        return []

    labels = detections["text_labels"]
    scores = detections["scores"].cpu().tolist()
    del detector, inputs, predictions, detections

    print(f"Segmenting {len(boxes)} detection(s) with {SEGMENTER}", flush=True)
    processor = Sam2Processor.from_pretrained(SEGMENTER)
    model = Sam2Model.from_pretrained(SEGMENTER, use_safetensors=True).to(device).eval()
    inputs = processor(images=image, input_boxes=[boxes], return_tensors="pt").to(device)
    with torch.inference_mode():
        outputs = model(**inputs, multimask_output=False)
        masks = processor.post_process_masks(
            outputs.pred_masks.cpu(), inputs["original_sizes"].cpu()
        )[0][:, 0]

    return list(zip(labels, scores, boxes, masks.numpy(), strict=True))


def write_results(output_dir, image, image_name, prompt, device, detections):
    from PIL import Image, ImageDraw

    output_dir.mkdir(parents=True, exist_ok=False)
    preview = image.copy()
    instances = []
    for instance_id, (label, score, box, mask) in enumerate(detections, start=1):
        mask_image = Image.fromarray(mask).convert("L")
        mask_name = f"mask_{instance_id:03d}.png"
        mask_image.save(output_dir / mask_name)
        color = COLORS[(instance_id - 1) % len(COLORS)]
        fill = Image.new("RGB", image.size, color)
        preview.paste(fill, mask=mask_image.point(lambda pixel: pixel * 2 // 5))
        instances.append({
            "instance_id": instance_id,
            "label": label,
            "detection_score": score,
            "bbox_xyxy": box,
            "mask": mask_name,
        })

    drawing = ImageDraw.Draw(preview)
    for instance in instances:
        instance_id = instance["instance_id"]
        box = instance["bbox_xyxy"]
        color = COLORS[(instance_id - 1) % len(COLORS)]
        drawing.rectangle(box, outline=color, width=3)
        drawing.text(
            (max(0, box[0]), max(0, box[1] - 14)),
            f"{instance_id}: {instance['label']}",
            fill=color,
            stroke_width=1,
            stroke_fill="black",
        )

    preview.save(output_dir / "overlay.jpg", quality=92)
    metadata = {
        "image": image_name,
        "width": image.width,
        "height": image.height,
        "pixel_coordinates": "EXIF-oriented input image, origin at top left",
        "prompt": prompt,
        "device": device,
        "detector": DETECTOR,
        "segmenter": SEGMENTER,
        "instances": instances,
    }
    (output_dir / "results.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Local JPEG or PNG image")
    parser.add_argument(
        "--prompt", default="blue cup. red box.", help="English object phrases separated by periods"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="New directory for masks, overlay, and JSON"
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--box-threshold", type=float, default=0.4)
    parser.add_argument("--text-threshold", type=float, default=0.3)
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"Image not found: {args.image}")
    if args.output_dir.exists():
        parser.error(f"Output directory already exists: {args.output_dir}")
    prompt = ". ".join(part.strip() for part in args.prompt.lower().split(".") if part.strip())
    if not prompt:
        parser.error("Provide at least one object phrase in --prompt")
    if not (0 <= args.box_threshold <= 1 and 0 <= args.text_threshold <= 1):
        parser.error("Thresholds must be between 0 and 1")
    prompt += "."

    from PIL import Image, ImageOps

    with Image.open(args.image) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    detections = segment(image, prompt, args.device, args.box_threshold, args.text_threshold)
    write_results(args.output_dir, image, args.image.name, prompt, args.device, detections)
    print(f"Saved {len(detections)} instance(s) to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
