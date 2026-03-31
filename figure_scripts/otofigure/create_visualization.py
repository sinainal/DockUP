#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Agg backend kullan - GUI gerektirmez
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.patches import Rectangle
from PIL import Image, ImageDraw
import glob
import re
import datetime
import argparse

# Ayarlanabilir parametreler
DEBUG_OUTPUT = False  # Debug görselinin kaydedilip kaydedilmeyeceğini kontrol eder (True/False)
DEFAULT_DPI = 300  # Çıkış DPI değeri
INPUT_DIR = "results"  # Giriş klasörü
OUTPUT_DIR = "final_results"  # Çıkış klasörü
INTERACTION_DIR = "interaction"  # Etkileşim haritaları klasörü
WIDTH_RATIOS = [4, 2, 3]  # Görsellerin genişlik oranları [far_view, close_view, interaction_map]
DEFAULT_FAR_FRAME_MARGIN = 0.03  # Far panelde görünür içerik etrafındaki ekstra crop payı
FIG_WIDTH = 14  # Figür genişliği (inç olarak)
PADDING_PERCENT = 7  # Kare etrafındaki ek boşluk (yüzde)
BORDER_THICKNESS = 1  # Çerçeve kalınlığı
CONNECTOR_THICKNESS = 0.75  # Bağlantı çizgilerinin kalınlığı
RED_CIRCLE_RADIUS = 7  # Kırmızı kürelerin yarıçapı
FAR_BOX_PADDING_PERCENT = 3.0
FAR_BOX_MIN_FOCUS_RATIO = 0.08
FAR_BOX_MIN_FOCUS_PX = 24


def _normalize_width_ratios(far_ratio=None, close_ratio=None, interaction_ratio=None):
    raw_values = [
        WIDTH_RATIOS[0] if far_ratio is None else far_ratio,
        WIDTH_RATIOS[1] if close_ratio is None else close_ratio,
        WIDTH_RATIOS[2] if interaction_ratio is None else interaction_ratio,
    ]
    normalized = []
    for value in raw_values:
        try:
            number = int(round(float(value)))
        except Exception:
            number = 1
        normalized.append(max(1, min(9, number)))
    return normalized


def _normalize_background_mode(raw_value):
    value = str(raw_value or "").strip().lower()
    if value in {"transparent", "white"}:
        return value
    return "transparent"


def _load_image_preserve_alpha(path):
    return cv2.imread(path, cv2.IMREAD_UNCHANGED)


def _as_bgr(image):
    if image is None:
        return None
    if len(image.shape) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def _as_rgba(image):
    if image is None:
        return None
    if len(image.shape) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)


def _opaque_color(image, bgr_color):
    if len(image.shape) == 3 and image.shape[2] == 4:
        return (*bgr_color, 255)
    return bgr_color


def _axis_bounds_in_pixels(ax, fig, canvas_width, canvas_height):
    bbox = ax.get_position()
    left = int(round(bbox.x0 * canvas_width))
    top = int(round((1.0 - bbox.y1) * canvas_height))
    right = int(round(bbox.x1 * canvas_width))
    bottom = int(round((1.0 - bbox.y0) * canvas_height))
    return left, top, right, bottom


def _fit_image_to_box(image, width, height):
    if width <= 0 or height <= 0:
        return None
    scale = min(float(width) / float(image.width), float(height) / float(image.height))
    new_width = max(1, int(round(image.width * scale)))
    new_height = max(1, int(round(image.height * scale)))
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def _trim_transparent_content(image, padding=28):
    if image is None:
        return image
    if len(image.shape) != 3 or image.shape[2] != 4:
        return image
    alpha = image[:, :, 3]
    points = cv2.findNonZero(alpha)
    if points is None:
        return image
    x, y, width, height = cv2.boundingRect(points)
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(image.shape[1], x + width + padding)
    bottom = min(image.shape[0], y + height + padding)
    return image[top:bottom, left:right].copy()


def _content_bbox(image, padding=0):
    if image is None:
        return None
    if len(image.shape) == 3 and image.shape[2] == 4:
        active = image[:, :, 3] > 10
    else:
        bgr = _as_bgr(image)
        active = np.any(bgr < 245, axis=2)
    points = cv2.findNonZero(active.astype(np.uint8))
    if points is None:
        return None
    x, y, width, height = cv2.boundingRect(points)
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(image.shape[1], x + width + padding)
    bottom = min(image.shape[0], y + height + padding)
    return left, top, right, bottom


def _crop_to_bbox(image, bbox):
    if image is None or bbox is None:
        return image
    left, top, right, bottom = bbox
    return image[top:bottom, left:right].copy()


def _alpha_compose_centered(canvas, image, box):
    left, top, right, bottom = box
    target = _fit_image_to_box(image, right - left, bottom - top)
    if target is None:
        return
    offset_x = left + max(0, ((right - left) - target.width) // 2)
    offset_y = top + max(0, ((bottom - top) - target.height) // 2)
    canvas.alpha_composite(target, dest=(offset_x, offset_y))


def _data_point_to_canvas(ax, fig, point, canvas_width, canvas_height):
    display = ax.transData.transform(point)
    fig_coord = fig.transFigure.inverted().transform(display)
    x = int(round(fig_coord[0] * canvas_width))
    y = int(round((1.0 - fig_coord[1]) * canvas_height))
    return x, y


def _draw_dashed_line(draw, start, end, *, dash=14, gap=8, width=1, fill=(0, 0, 0, 255)):
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length = float(np.hypot(dx, dy))
    if length <= 0.0:
        return
    step_x = dx / length
    step_y = dy / length
    progress = 0.0
    while progress < length:
        seg_start = progress
        seg_end = min(progress + dash, length)
        sx = x1 + step_x * seg_start
        sy = y1 + step_y * seg_start
        ex = x1 + step_x * seg_end
        ey = y1 + step_y * seg_end
        draw.line((sx, sy, ex, ey), fill=fill, width=width)
        progress += dash + gap

def find_rgb_regions(image, *, padding_percent=PADDING_PERCENT, min_focus_ratio=0.18, min_focus_px=44):
    """RGB/renkli bölgeleri tespit et ve kare kordinatlarını döndür"""
    bgr_image = _as_bgr(image)
    hsv_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
    max_channel = rgb_image.max(axis=2).astype(np.int16)
    min_channel = rgb_image.min(axis=2).astype(np.int16)
    chroma = max_channel - min_channel
    value = hsv_image[:, :, 2].astype(np.int16)
    saturation = hsv_image[:, :, 1].astype(np.int16)
    alpha_mask = np.ones(rgb_image.shape[:2], dtype=np.uint8) * 255
    if len(image.shape) == 3 and image.shape[2] == 4:
        alpha_mask = image[:, :, 3]

    contours = []
    candidate_masks = [
        (saturation >= 40) & (value >= 20),
        (saturation >= 26) & (value >= 18),
        (chroma >= 22) & (value >= 28),
    ]
    for active in candidate_masks:
        mask = np.zeros_like(saturation, dtype=np.uint8)
        mask[(alpha_mask > 18) & active] = 255
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [contour for contour in contours if cv2.contourArea(contour) >= 6.0]
        if contours:
            break
    
    # Eğer kontür bulunamazsa varsayılan bir kare döndür
    if not contours:
        size = min(image.shape[0], image.shape[1]) // 3
        x = (image.shape[1] - size) // 2
        y = (image.shape[0] - size) // 2
        return x, y, size, []
    
    # Tüm kontür noktaları
    all_points = np.concatenate([contour for contour in contours])
    x_min = np.min(all_points[:, 0, 0])
    y_min = np.min(all_points[:, 0, 1])
    x_max = np.max(all_points[:, 0, 0])
    y_max = np.max(all_points[:, 0, 1])
    
    # Genişlik, yükseklik ve büyük kenar
    width = x_max - x_min
    height = y_max - y_min
    max_side = max(width, height)
    min_focus_size = max(int(min_focus_px), int(round(min(image.shape[0], image.shape[1]) * float(min_focus_ratio))))
    max_side = max(max_side, min_focus_size)
    
    # Kutuyu bbox orta noktasına değil, gerçek görünür piksellerin ağırlık merkezine hizala.
    focus_mask = np.zeros_like(saturation, dtype=np.uint8)
    for contour in contours:
        cv2.drawContours(focus_mask, [contour], -1, 255, thickness=-1)
    moments = cv2.moments(focus_mask, binaryImage=True)
    if moments["m00"]:
        center_x = int(round(moments["m10"] / moments["m00"]))
        center_y = int(round(moments["m01"] / moments["m00"]))
    else:
        center_x = (x_min + x_max) // 2
        center_y = (y_min + y_max) // 2
    
    # Padding ekle
    padding = int(max_side * float(padding_percent) / 100)
    padded_size = max_side + 2 * padding
    
    # Kare koordinatlarını hesapla
    x = max(0, center_x - padded_size // 2)
    y = max(0, center_y - padded_size // 2)
    
    # Sınırlar dahilinde boyutu ayarla
    size = min(image.shape[1] - x, image.shape[0] - y, padded_size)
    
    return x, y, size, contours

def connect_points_directly(fig, ax1, ax2, point1, point2, color='black', linestyle='--', linewidth=0.75):
    """
    İki figür ekseninde noktaları doğrudan birleştir.
    
    fig: matplotlib figür objesi
    ax1, ax2: noktaların bulunduğu eksenler
    point1: birinci noktanın eksen içindeki (x, y) koordinatları
    point2: ikinci noktanın eksen içindeki (x, y) koordinatları
    """
    # Noktanın veri konum koordinatlarından görüntü koordinatlarına
    display1 = ax1.transData.transform(point1)
    display2 = ax2.transData.transform(point2)
    
    # Görüntü koordinatlarından figure koordinatlarına
    fig_coord1 = fig.transFigure.inverted().transform(display1)
    fig_coord2 = fig.transFigure.inverted().transform(display2)
    
    # Çizgiyi figure koordinatlarında çiz
    line = plt.Line2D([fig_coord1[0], fig_coord2[0]], 
                      [fig_coord1[1], fig_coord2[1]], 
                      transform=fig.transFigure, 
                      color=color, 
                      linestyle=linestyle, 
                      linewidth=linewidth)
    fig.add_artist(line)

def create_blank_image_with_text(width, height, text="Interaction map not found"):
    """
    Belirtilen boyutlarda beyaz bir görüntü oluşturur ve ortasına metin ekler
    
    Args:
        width: Görüntü genişliği
        height: Görüntü yüksekliği
        text: Görüntüye eklenecek metin
        
    Returns:
        numpy.ndarray: Oluşturulan görüntü
    """
    # Şeffaf arka planlı boş bir görüntü oluştur
    blank_image = np.zeros((height, width, 4), dtype=np.uint8)
    
    # Metin parametreleri
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.0
    font_color = (100, 100, 100, 255)  # Gri renk
    thickness = 2
    
    # Metin boyutunu hesapla
    text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
    
    # Metni görüntünün ortasına yerleştir
    x = (width - text_size[0]) // 2
    y = (height + text_size[1]) // 2
    
    # Metni görüntüye ekle
    cv2.putText(blank_image, text, (x, y), font, font_scale, font_color, thickness)
    
    return blank_image

def create_visualization(
    input_filename,
    output_dir=OUTPUT_DIR,
    interaction_dir=INTERACTION_DIR,
    debug=False,
    dpi=DEFAULT_DPI,
    far_ratio=None,
    close_ratio=None,
    interaction_ratio=None,
    background_mode="transparent",
    far_frame_margin=DEFAULT_FAR_FRAME_MARGIN,
):
    """
    Belirtilen dosyaları kullanarak görselleştirmeyi oluştur
    
    Args:
        input_filename: İşlenecek dosyanın tam yolu
        output_dir: Çıkış klasörü
        interaction_dir: Etkileşim haritaları klasörü
        debug: Debug modu
    
    Returns:
        bool: İşlem başarılıysa True, değilse False
    """
    global DEBUG_OUTPUT
    DEBUG_OUTPUT = debug
    width_ratios = _normalize_width_ratios(far_ratio, close_ratio, interaction_ratio)
    background_mode = _normalize_background_mode(background_mode)
    canvas_background = (255, 255, 255, 0) if background_mode == "transparent" else (255, 255, 255, 255)
    
    try:
        # Dosya adından bilgileri çıkar
        filename = os.path.basename(input_filename)
        parts = filename.split('_')
        
        if len(parts) < 3:
            print(f"Hata: Dosya adı uygun formatta değil: {filename}")
            return False
        
        pdb_id = parts[0]
        zinc_id = parts[1]
        view_type = parts[2].split('.')[0]  # far veya close
        
        # Far ve close görüntülerinin yollarını belirle
        base_dir = os.path.dirname(input_filename)
        far_view_path = os.path.join(base_dir, f"{pdb_id}_{zinc_id}_far.png")
        close_view_path = os.path.join(base_dir, f"{pdb_id}_{zinc_id}_close.png")
        far_focus_path = os.path.join(base_dir, f"{pdb_id}_{zinc_id}_far_focus.png")
        
        # Interaction haritası dosya yolunu belirle
        interaction_map_path = os.path.join(interaction_dir, f"{pdb_id}_{zinc_id}_interaction.png")
        interaction_map_exists = os.path.exists(interaction_map_path)
        
        # Dosyaların varlığını kontrol et
        if not os.path.exists(far_view_path) or not os.path.exists(close_view_path):
            print(f"Hata: Far veya close görüntüsü bulunamadı: {far_view_path}, {close_view_path}")
            return False
        
        # Görüntüleri yükle
        far_view = _load_image_preserve_alpha(far_view_path)
        close_view = _load_image_preserve_alpha(close_view_path)
        
        if far_view is None or close_view is None:
            print(f"Hata: Görüntüler okunamadı - Far: {far_view_path}, Close: {close_view_path}")
            return False
        
        original_far_shape = far_view.shape[:2]

        # FAR_VIEW için mümkünse ligand-only yardımcı çıktıyı kullan
        far_focus_view = far_view
        if os.path.exists(far_focus_path):
            helper_image = _load_image_preserve_alpha(far_focus_path)
            if helper_image is not None:
                far_focus_view = helper_image

        try:
            far_frame_margin = max(0.0, min(0.15, float(far_frame_margin)))
        except Exception:
            far_frame_margin = DEFAULT_FAR_FRAME_MARGIN
        far_crop_padding = max(0, int(round(min(far_view.shape[0], far_view.shape[1]) * far_frame_margin)))
        far_crop_bbox = _content_bbox(far_view, padding=far_crop_padding)
        if far_crop_bbox is not None:
            far_view = _crop_to_bbox(far_view, far_crop_bbox)
            if far_focus_view is not None and far_focus_view.shape[:2] == original_far_shape:
                far_focus_view = _crop_to_bbox(far_focus_view, far_crop_bbox)

        # Interaction haritasını yükle veya boş bir görüntü oluştur
        if interaction_map_exists:
            interaction_map = _load_image_preserve_alpha(interaction_map_path)
            if interaction_map is None:
                print(f"Uyarı: Interaction haritası yüklenemedi, boş görüntü kullanılacak: {interaction_map_path}")
                interaction_map = create_blank_image_with_text(close_view.shape[1], close_view.shape[0])
        else:
            print(f"Uyarı: Interaction haritası bulunamadı, boş görüntü kullanılacak: {interaction_map_path}")
            interaction_map = create_blank_image_with_text(close_view.shape[1], close_view.shape[0])
        interaction_map = _trim_transparent_content(interaction_map)
        
        # CLOSE_VIEW'da RGB bölgelerini bul ve kırp
        x_close, y_close, size_close, _ = find_rgb_regions(close_view)
        cropped_close = close_view[y_close:y_close+size_close, x_close:x_close+size_close].copy()
        cv2.rectangle(
            cropped_close,
            (0, 0),
            (size_close-1, size_close-1),
            _opaque_color(cropped_close, (0, 0, 0)),
            BORDER_THICKNESS,
        )

        # 2. görselin sol köşelerini kırmızı kürelerle işaretleyecek işaretleme kopyası
        debug_close_view = _as_bgr(cropped_close.copy())
        # Sol üst köşe - 1. görsel ile aynı boyutta olması için RED_CIRCLE_RADIUS değerini kullan
        cv2.circle(debug_close_view, (0, 0), RED_CIRCLE_RADIUS, (0, 0, 255), -1)
        # Sol alt köşe - 1. görsel ile aynı boyutta olması için RED_CIRCLE_RADIUS değerini kullan
        cv2.circle(debug_close_view, (0, size_close-1), RED_CIRCLE_RADIUS, (0, 0, 255), -1)

        # FAR_VIEW'da RGB bölgelerini bul
        x_far, y_far, size_far, far_contours = find_rgb_regions(
            far_focus_view,
            padding_percent=FAR_BOX_PADDING_PERCENT,
            min_focus_ratio=FAR_BOX_MIN_FOCUS_RATIO,
            min_focus_px=FAR_BOX_MIN_FOCUS_PX,
        )
        
        # DEBUG GÖRSELİ İÇİN FAR_VIEW KOPYASI
        debug_far_view = _as_bgr(far_view.copy())
        
        # RGB bölgelerini renklendir
        for contour in far_contours:
            cv2.drawContours(debug_far_view, [contour], -1, (0, 255, 0), 2)  # Yeşil renk
        
        # Kareyi çiz
        cv2.rectangle(debug_far_view, (x_far, y_far), (x_far + size_far, y_far + size_far), (0, 0, 0), BORDER_THICKNESS)
        
        # Köşe noktalarını kırmızı kürelerle işaretle
        right_top = (x_far + size_far, y_far)
        right_bottom = (x_far + size_far, y_far + size_far)
        cv2.circle(debug_far_view, right_top, RED_CIRCLE_RADIUS, (0, 0, 255), -1)  # Kırmızı küre
        cv2.circle(debug_far_view, right_bottom, RED_CIRCLE_RADIUS, (0, 0, 255), -1)  # Kırmızı küre
        
        # OpenCV/NumPy -> matplotlib RGBA/RGB dönüşümleri
        debug_far_view_rgb = cv2.cvtColor(debug_far_view, cv2.COLOR_BGR2RGB)
        debug_close_view_rgb = cv2.cvtColor(debug_close_view, cv2.COLOR_BGR2RGB)
        far_view_rgb = _as_rgba(far_view)
        cropped_close_rgb = _as_rgba(cropped_close)
        interaction_map_rgb = _as_rgba(interaction_map)
    
        # Figür boyutlarını hesapla
        fig_width = FIG_WIDTH
        
        # En/boy oranını koruyarak figür yüksekliğini hesapla
        normalized_heights = []
        for i, ratio in enumerate(width_ratios):
            if i == 0:
                normalized_heights.append(far_view.shape[0] / far_view.shape[1] * ratio)
            elif i == 1:
                normalized_heights.append(cropped_close.shape[0] / cropped_close.shape[1] * ratio)
            elif i == 2:
                normalized_heights.append(interaction_map.shape[0] / interaction_map.shape[1] * ratio)
        
        # Yükseklik faktörünü hesapla
        height_factor = max(normalized_heights) / sum(width_ratios)
        
        # Figür yüksekliğini hesapla
        fig_height = fig_width * height_factor * 1.2  # 1.2 faktörü biraz daha yükseklik ekler
        
        # Kırmızı noktaların koordinatları (görüntü içinde)
        far_right_top_data = (right_top[0], right_top[1])  # Sağ üst köşe
        far_right_bottom_data = (right_bottom[0], right_bottom[1])  # Sağ alt köşe
        
        close_left_top_data = (0, 0)  # Sol üst köşe
        close_left_bottom_data = (0, size_close-1)  # Sol alt köşe
        
        # ----- DEBUG GÖRSELİ OLUŞTUR -----
        if DEBUG_OUTPUT:
            plt.ioff()  # Etkileşimli modu kapat
            fig, axs = plt.subplots(1, 3, figsize=(fig_width, fig_height), gridspec_kw={'width_ratios': width_ratios})
            if background_mode == "transparent":
                fig.patch.set_alpha(0)
            else:
                fig.patch.set_facecolor((1, 1, 1, 1))
            for ax in axs:
                if background_mode == "transparent":
                    ax.set_facecolor((1, 1, 1, 0))
                    ax.patch.set_alpha(0)
                else:
                    ax.set_facecolor((1, 1, 1, 1))
                    ax.patch.set_alpha(1)
            
            # RGB bölgeleri ve kırmızı kürelerle işaretlenmiş far_view
            axs[0].imshow(debug_far_view_rgb)
            axs[0].axis('off')
            
            # Close view (kırpılmış, kırmızı kürelerle)
            axs[1].imshow(debug_close_view_rgb)
            axs[1].axis('off')
            
            # Interaction map
            axs[2].imshow(interaction_map_rgb)
            axs[2].axis('off')
            
            # Görüntüleri çizdikten sonra figürün kesin boyutlarını alalım
            plt.tight_layout()
            fig.canvas.draw()
            
            # Doğrudan koordinat dönüşümü yaparak çizgileri çiz
            connect_points_directly(
                fig, axs[0], axs[1], 
                far_right_top_data, close_left_top_data, 
                color='black', linestyle='--', linewidth=CONNECTOR_THICKNESS
            )
            
            connect_points_directly(
                fig, axs[0], axs[1], 
                far_right_bottom_data, close_left_bottom_data, 
                color='black', linestyle='--', linewidth=CONNECTOR_THICKNESS
            )
            
            # Debug görselini kaydet
            output_filename = f"{pdb_id}_{zinc_id}_debug.png"
            debug_output_file = os.path.join(output_dir, output_filename)
            plt.savefig(debug_output_file, dpi=dpi, bbox_inches='tight', pad_inches=0, transparent=True)
            print(f"- Debug görsel: {debug_output_file}")
            
            plt.close(fig)
        
        # ----- FINAL GÖRSELİ OLUŞTUR -----
        plt.ioff()  # Etkileşimli modu kapat
        fig, axs = plt.subplots(1, 3, figsize=(fig_width, fig_height), gridspec_kw={'width_ratios': width_ratios})
        if background_mode == "transparent":
            fig.patch.set_alpha(0)
        else:
            fig.patch.set_facecolor((1, 1, 1, 1))
        for ax in axs:
            if background_mode == "transparent":
                ax.set_facecolor((1, 1, 1, 0))
                ax.patch.set_alpha(0)
            else:
                ax.set_facecolor((1, 1, 1, 1))
                ax.patch.set_alpha(1)
        
        # Orijinal far_view
        axs[0].imshow(far_view_rgb)
        axs[0].axis('off')
        
        # Sadece kareyi ekle
        rect = Rectangle((x_far, y_far), size_far, size_far, linewidth=BORDER_THICKNESS, edgecolor='black', facecolor='none')
        axs[0].add_patch(rect)
        
        # Orijinal close view (kırpılmış)
        axs[1].imshow(cropped_close_rgb)
        axs[1].axis('off')
        
        # Interaction map
        axs[2].imshow(interaction_map_rgb)
        axs[2].axis('off')
        
        # Görüntüleri çizdikten sonra figürün kesin boyutlarını alalım
        plt.tight_layout()
        fig.canvas.draw()
        
        # Aynı koordinatları kullanarak çizgileri çiz (kırmızı noktasız)
        canvas_width = int(round(fig.get_figwidth() * dpi))
        canvas_height = int(round(fig.get_figheight() * dpi))
        pil_canvas = Image.new("RGBA", (canvas_width, canvas_height), canvas_background)

        far_image = Image.fromarray(far_view_rgb, mode="RGBA")
        close_image = Image.fromarray(cropped_close_rgb, mode="RGBA")
        interaction_image = Image.fromarray(interaction_map_rgb, mode="RGBA")

        for ax, image in zip(axs, (far_image, close_image, interaction_image)):
            _alpha_compose_centered(
                pil_canvas,
                image,
                _axis_bounds_in_pixels(ax, fig, canvas_width, canvas_height),
            )

        overlay = Image.new("RGBA", pil_canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        far_box_top_left = _data_point_to_canvas(
            axs[0],
            fig,
            (x_far, y_far),
            canvas_width,
            canvas_height,
        )
        far_box_bottom_right = _data_point_to_canvas(
            axs[0],
            fig,
            (x_far + size_far, y_far + size_far),
            canvas_width,
            canvas_height,
        )
        draw.rectangle(
            (far_box_top_left, far_box_bottom_right),
            outline=(0, 0, 0, 255),
            width=max(1, int(round(BORDER_THICKNESS))),
        )

        connector_start_top = _data_point_to_canvas(
            axs[0],
            fig,
            far_right_top_data,
            canvas_width,
            canvas_height,
        )
        connector_start_bottom = _data_point_to_canvas(
            axs[0],
            fig,
            far_right_bottom_data,
            canvas_width,
            canvas_height,
        )
        connector_end_top = _data_point_to_canvas(
            axs[1],
            fig,
            close_left_top_data,
            canvas_width,
            canvas_height,
        )
        connector_end_bottom = _data_point_to_canvas(
            axs[1],
            fig,
            close_left_bottom_data,
            canvas_width,
            canvas_height,
        )
        dash_width = max(1, int(round(CONNECTOR_THICKNESS)))
        _draw_dashed_line(draw, connector_start_top, connector_end_top, width=dash_width)
        _draw_dashed_line(draw, connector_start_bottom, connector_end_bottom, width=dash_width)

        pil_canvas.alpha_composite(overlay)

        # Final görselini kaydet (sadece PNG olarak)
        output_filename = f"{pdb_id}_{zinc_id}_final.png"
        final_output_file = os.path.join(output_dir, output_filename)
        pil_canvas.save(final_output_file, dpi=(dpi, dpi))
        plt.close(fig)
        
        print(f"Görselleştirme tamamlandı: {final_output_file}")
        return True
    
    except Exception as e:
        print(f"Hata: Görselleştirme oluşturulurken bir hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def process_results_dir(
    input_dir=INPUT_DIR,
    output_dir=OUTPUT_DIR,
    interaction_dir=INTERACTION_DIR,
    debug=False,
    dpi=DEFAULT_DPI,
    far_ratio=None,
    close_ratio=None,
    interaction_ratio=None,
    background_mode="transparent",
    far_frame_margin=DEFAULT_FAR_FRAME_MARGIN,
):
    """
    Results klasöründeki tüm PNG dosyalarını işler.
    
    Args:
        input_dir: Giriş klasörü
        output_dir: Çıkış klasörü
        interaction_dir: Etkileşim haritaları klasörü
        debug: Debug modu
    """
    # Çıkış klasörünü oluştur
    os.makedirs(output_dir, exist_ok=True)
    
    # Log dosyası oluştur
    log_filename = f"visualization_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    log_filepath = os.path.join(output_dir, log_filename)
    
    success_count = 0
    error_count = 0
    processed_combinations = set()  # İşlenen kombinasyonların takibi için küme
    
    with open(log_filepath, 'w') as log_file:
        log_message = f"Görselleştirme işlemi başladı: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        print(log_message)
        log_file.write(log_message + "\n")
        
        # Results klasöründeki tüm PNG dosyalarını bul
        png_files = glob.glob(os.path.join(input_dir, "*.png"))
        
        print(f"Toplam {len(png_files)} PNG dosyası bulundu.")
        
        for png_file in png_files:
            filename = os.path.basename(png_file)
            parts = filename.split('_')
            
            if len(parts) < 3:
                log_message = f"Atlanıyor: {filename} - Dosya adı uygun formatta değil"
                print(log_message)
                log_file.write(log_message + "\n")
                continue
            
            pdb_id = parts[0]
            zinc_id = parts[1]
            
            # Bu kombinasyon daha önce işlendiyse atla
            if (pdb_id, zinc_id) in processed_combinations:
                continue
            
            try:
                log_message = f"İşleniyor: {pdb_id}_{zinc_id}"
                print(log_message)
                log_file.write(log_message + "\n")
                
                # Görselleştirmeyi oluştur
                result = create_visualization(
                    png_file,
                    output_dir,
                    interaction_dir,
                    debug,
                    dpi=dpi,
                    far_ratio=far_ratio,
                    close_ratio=close_ratio,
                    interaction_ratio=interaction_ratio,
                    background_mode=background_mode,
                    far_frame_margin=far_frame_margin,
                )
                
                # Kombinasyonu işlenmiş olarak işaretle
                processed_combinations.add((pdb_id, zinc_id))
                
                if result:
                    success_count += 1
                    log_message = f"Başarılı: {pdb_id}_{zinc_id}_final.png oluşturuldu"
                    print(log_message)
                    log_file.write(log_message + "\n")
                else:
                    error_count += 1
                    log_message = f"Hata: {pdb_id}_{zinc_id}_final.png oluşturulamadı"
                    print(log_message)
                    log_file.write(log_message + "\n")
            
            except Exception as e:
                error_count += 1
                log_message = f"Beklenmeyen hata: {filename} - {str(e)}"
                print(log_message)
                log_file.write(log_message + "\n")
        
        log_message = f"\nİşleme sonuçları:"
        print(log_message)
        log_file.write(log_message + "\n")
        log_file.write(f"Toplam işlenen: {len(processed_combinations)}\n")
        log_file.write(f"Başarılı: {success_count}\n")
        log_file.write(f"Hatalı: {error_count}\n")
    
    print(f"Log dosyası oluşturuldu: {log_filepath}")
    return success_count > 0

def main():
    # Komut satırı argümanlarını işle
    parser = argparse.ArgumentParser(description='Protein-ligand görselleştirmesi oluşturur.')
    parser.add_argument('--input_dir', type=str, default=INPUT_DIR, help='Giriş klasörünün yolu')
    parser.add_argument('--output_dir', type=str, default=OUTPUT_DIR, help='Çıkış klasörünün yolu')
    parser.add_argument('--interaction_dir', type=str, default=INTERACTION_DIR, help='Etkileşim haritaları klasörünün yolu')
    parser.add_argument('--dpi', type=int, default=DEFAULT_DPI, help='Çıkış DPI değeri')
    parser.add_argument('--far-ratio', type=int, default=WIDTH_RATIOS[0], help='Far panel width ratio')
    parser.add_argument('--close-ratio', type=int, default=WIDTH_RATIOS[1], help='Close panel width ratio')
    parser.add_argument('--interaction-ratio', type=int, default=WIDTH_RATIOS[2], help='Interaction panel width ratio')
    parser.add_argument('--far-frame-margin', type=float, default=DEFAULT_FAR_FRAME_MARGIN, help='Extra crop margin around visible far-panel content')
    parser.add_argument('--background', type=str, default='transparent', help='Final background: transparent/white')
    parser.add_argument('--debug', action='store_true', help='Debug modunu etkinleştir')
    
    args = parser.parse_args()
    
    # Results klasöründeki tüm PNG dosyalarını işle
    process_results_dir(
        args.input_dir,
        args.output_dir,
        args.interaction_dir,
        args.debug,
        dpi=args.dpi,
        far_ratio=args.far_ratio,
        close_ratio=args.close_ratio,
        interaction_ratio=args.interaction_ratio,
        far_frame_margin=args.far_frame_margin,
        background_mode=args.background,
    )

if __name__ == "__main__":
    main() 
