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
DPI = 300  # Çıkış DPI değeri
INPUT_DIR = "results"  # Giriş klasörü
OUTPUT_DIR = "final_results"  # Çıkış klasörü
INTERACTION_DIR = "interaction"  # Etkileşim haritaları klasörü
WIDTH_RATIOS = [4, 3, 3]  # Görsellerin genişlik oranları [far_view, close_view, interaction_map]
FIG_WIDTH = 14  # Figür genişliği (inç olarak)
PADDING_PERCENT = 7  # Kare etrafındaki ek boşluk (yüzde)
BORDER_THICKNESS = 1  # Çerçeve kalınlığı
CONNECTOR_THICKNESS = 0.75  # Bağlantı çizgilerinin kalınlığı
RED_CIRCLE_RADIUS = 7  # Kırmızı kürelerin yarıçapı


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

def find_rgb_regions(image):
    """RGB/renkli bölgeleri tespit et ve kare kordinatlarını döndür"""
    # BGR'dan RGB'ye dönüştür
    rgb_image = cv2.cvtColor(_as_bgr(image), cv2.COLOR_BGR2RGB)
    
    # Gri skala sapma toleransı
    TOLERANCE = 40  # Renk kanalları arası fark bu değerden azsa gri kabul edilecek
    MIN_INTENSITY = 80  # Bundan daha koyu renkler karanlık kabul edilecek
    
    # Kanalları ayır
    r_channel = rgb_image[:, :, 0].astype(np.int16)
    g_channel = rgb_image[:, :, 1].astype(np.int16)
    b_channel = rgb_image[:, :, 2].astype(np.int16)
    
    # Gri skala kontrolü - her renk kanalı arasındaki maksimum fark
    r_g_diff = np.abs(r_channel - g_channel)
    r_b_diff = np.abs(r_channel - b_channel)
    g_b_diff = np.abs(g_channel - b_channel)
    
    max_diff = np.maximum(np.maximum(r_g_diff, r_b_diff), g_b_diff)
    
    # Renkli pikselleri bul (gri olmayanlar)
    mask = np.zeros_like(r_channel, dtype=np.uint8)
    
    # Koyu olmayan ve gri olmayan pikselleri işaretle
    non_dark = (r_channel >= MIN_INTENSITY) | (g_channel >= MIN_INTENSITY) | (b_channel >= MIN_INTENSITY)
    non_gray = max_diff > TOLERANCE
    
    # Hem koyu olmayan hem de gri olmayan pikselleri işaretle
    mask[non_dark & non_gray] = 255
    
    # Morfolojik işlemler
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    
    # Kontürleri bul
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
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
    
    # Merkez hesapla
    center_x = (x_min + x_max) // 2
    center_y = (y_min + y_max) // 2
    
    # Padding ekle
    padding = int(max_side * PADDING_PERCENT / 100)
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

def create_visualization(input_filename, output_dir=OUTPUT_DIR, interaction_dir=INTERACTION_DIR, debug=False):
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
        
        # Interaction haritasını yükle veya boş bir görüntü oluştur
        if interaction_map_exists:
            interaction_map = _load_image_preserve_alpha(interaction_map_path)
            if interaction_map is None:
                print(f"Uyarı: Interaction haritası yüklenemedi, boş görüntü kullanılacak: {interaction_map_path}")
                interaction_map = create_blank_image_with_text(close_view.shape[1], close_view.shape[0])
        else:
            print(f"Uyarı: Interaction haritası bulunamadı, boş görüntü kullanılacak: {interaction_map_path}")
            interaction_map = create_blank_image_with_text(close_view.shape[1], close_view.shape[0])
        
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
        x_far, y_far, size_far, far_contours = find_rgb_regions(far_view)
        
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
        for i, ratio in enumerate(WIDTH_RATIOS):
            if i == 0:
                normalized_heights.append(far_view.shape[0] / far_view.shape[1] * ratio)
            elif i == 1:
                normalized_heights.append(cropped_close.shape[0] / cropped_close.shape[1] * ratio)
            elif i == 2:
                normalized_heights.append(interaction_map.shape[0] / interaction_map.shape[1] * ratio)
        
        # Yükseklik faktörünü hesapla
        height_factor = max(normalized_heights) / sum(WIDTH_RATIOS)
        
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
            fig, axs = plt.subplots(1, 3, figsize=(fig_width, fig_height), gridspec_kw={'width_ratios': WIDTH_RATIOS})
            fig.patch.set_alpha(0)
            for ax in axs:
                ax.set_facecolor((1, 1, 1, 0))
                ax.patch.set_alpha(0)
            
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
            plt.savefig(debug_output_file, dpi=DPI, bbox_inches='tight', pad_inches=0, transparent=True)
            print(f"- Debug görsel: {debug_output_file}")
            
            plt.close(fig)
        
        # ----- FINAL GÖRSELİ OLUŞTUR -----
        plt.ioff()  # Etkileşimli modu kapat
        fig, axs = plt.subplots(1, 3, figsize=(fig_width, fig_height), gridspec_kw={'width_ratios': WIDTH_RATIOS})
        fig.patch.set_alpha(0)
        for ax in axs:
            ax.set_facecolor((1, 1, 1, 0))
            ax.patch.set_alpha(0)
        
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
        canvas_width = int(round(fig.get_figwidth() * DPI))
        canvas_height = int(round(fig.get_figheight() * DPI))
        pil_canvas = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))

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
        pil_canvas.save(final_output_file, dpi=(DPI, DPI))
        plt.close(fig)
        
        print(f"Görselleştirme tamamlandı: {final_output_file}")
        return True
    
    except Exception as e:
        print(f"Hata: Görselleştirme oluşturulurken bir hata oluştu: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def process_results_dir(input_dir=INPUT_DIR, output_dir=OUTPUT_DIR, interaction_dir=INTERACTION_DIR, debug=False):
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
                result = create_visualization(png_file, output_dir, interaction_dir, debug)
                
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
    parser.add_argument('--debug', action='store_true', help='Debug modunu etkinleştir')
    
    args = parser.parse_args()
    
    # Results klasöründeki tüm PNG dosyalarını işle
    process_results_dir(args.input_dir, args.output_dir, args.interaction_dir, args.debug)

if __name__ == "__main__":
    main() 
