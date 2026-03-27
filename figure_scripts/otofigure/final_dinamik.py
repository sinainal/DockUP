#!/usr/bin/env python

import os
import math
import glob
import argparse
import sys
from pymol import cmd
import numpy as np

# ==========================================
# KULLANICI TARAFINDAN AYARLANABİLİR DEĞERLER
# ==========================================

# Görüntü Ayarları
IMAGE_WIDTH = 100       # Görsel genişliği (piksel)
IMAGE_HEIGHT = 100       # Görsel yüksekliği (piksel)
IMAGE_DPI = 10          # Görsel çözünürlüğü (DPI)

# Padding Ayarları (Görünüm alanı payı)
# Not: Bu değerler, hesaplanan protein/ligand köşegeninin çarpım faktörüdür
FAR_PADDING_FACTOR = 0.03    # Uzak görünüm payı (proteinin köşegeni × bu faktör)
CLOSE_PADDING_FACTOR = 0.2   # Yakın görünüm payı (ligandların köşegeni × bu faktör)

# Sabit değerler istenirse, bu değişkenleri kullanın (0 = otomatik hesaplama)
FIXED_FAR_PADDING = 0     # 0 = otomatik, >0 = sabit değer (Angstrom)
FIXED_CLOSE_PADDING = -100   # 0 = otomatik, >0 = sabit değer (Angstrom)

# Debug modu - daha fazla bilgi gösterir
DEBUG_MODE = False

# Ağırlık merkezleri görünüm ayarları
SHOW_CENTERS = False     # Ağırlık merkezlerini göster/gizle
CENTER_SPHERE_SIZE = 1.0  # Ağırlık merkezi küre boyutu
VECTOR_CYLINDER_RADIUS = 0.3  # Merkezler arası vektör kalınlığı

# Ligand dik pozisyon ayarları
Z_ROTATION_ANGLE = 90   # Z ekseni etrafında rotasyon açısı (derece)

# ==========================================

def bul_protein_dosyasi(protein_klasoru):
    """
    Protein klasöründeki ilk .pdb dosyasını bulur.
    
    Args:
        protein_klasoru (str): Protein dosyalarının bulunduğu klasör yolu
        
    Returns:
        str: Bulunan protein dosyasının tam yolu veya bulunamazsa None
    """
    pdb_dosyalari = glob.glob(os.path.join(protein_klasoru, "*.pdb"))
    if pdb_dosyalari:
        return pdb_dosyalari[0]  # İlk bulunan PDB dosyasını döndür
    return None

def final_gorselleştirme(pdb_id=None, ligands_dir=None, output_dir="results", image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, image_dpi=IMAGE_DPI):
    """
    Hizala3.py'nin poz mekanizması ve renkler.py'nin renk ayarlarını birleştiren sade kod.
    Protein ve ligand boyutlarına göre padding'i otomatik hesaplar.
    Uzak (far) ve yakın (close) olmak üzere iki farklı poz kaydeder.
    Ayrıca her iki poz için belirtilen dpi çözünürlükte render edilmiş görseller oluşturur.
    
    Args:
        pdb_id (str): Protein yapısının PDB ID'si
        ligands_dir (str): Ligand dosyalarının bulunduğu klasör yolu
        output_dir (str): Çıktı dosyalarının kaydedileceği klasör
        image_width (int): Görsel genişliği (piksel)
        image_height (int): Görsel yüksekliği (piksel)
        image_dpi (int): Görsel çözünürlüğü (DPI)
    
    Returns:
        bool: İşlemin başarılı olup olmadığı
    """
    global IMAGE_WIDTH, IMAGE_HEIGHT, IMAGE_DPI
    
    # Parametreleri güncelle
    IMAGE_WIDTH = image_width
    IMAGE_HEIGHT = image_height
    IMAGE_DPI = image_dpi
    
    print("Görsel ayarları:")
    print(f"  * Boyut: {IMAGE_WIDTH}x{IMAGE_HEIGHT} piksel")
    print(f"  * Çözünürlük: {IMAGE_DPI} DPI")
    print("Padding ayarları:")
    if FIXED_FAR_PADDING > 0:
        print(f"  * Uzak görünüm payı: {FIXED_FAR_PADDING} Å (sabit)")
    else:
        print(f"  * Uzak görünüm payı: protein köşegeninin {FAR_PADDING_FACTOR*100}%'i")
    
    if FIXED_CLOSE_PADDING > 0:
        print(f"  * Yakın görünüm payı: {FIXED_CLOSE_PADDING} Å (sabit)")
    else:
        print(f"  * Yakın görünüm payı: ligand köşegeninin {CLOSE_PADDING_FACTOR*100}%'i")
    
    print(f"Ligand pozisyon ayarları:")
    print(f"  * Z ekseni rotasyon açısı: {Z_ROTATION_ANGLE} derece")
    
    # Çalışma klasörünü belirle
    current_dir = os.getcwd()
    
    # PDB ID ve ligands_dir parametrelerini kontrol et
    if pdb_id is None or ligands_dir is None:
        # Varsayılan klasör yapısını kullan
        protein_dir = os.path.join(current_dir, "protein")
        if ligands_dir is None:
            ligands_dir = os.path.join(current_dir, "ligands")
    else:
        # Protein klasörü, pdb_id olarak belirlenir
        protein_dir = os.path.join(current_dir, "protein")
        # Ligand klasörü parametre olarak verilir
    
    # Çıktı klasörünü oluştur
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # PyMOL'u baştan başlat
    cmd.reinitialize()
    
    # Protein yapısını yükle - otomatik olarak ilk PDB dosyasını bul
    protein_file = bul_protein_dosyasi(protein_dir)
    
    if not protein_file:
        print("Hata: Protein klasöründe PDB dosyası bulunamadı!")
        return False
    
    # PDB ID yoksa, protein dosya adından çıkar
    if pdb_id is None:
        pdb_id = os.path.basename(protein_file).split(".")[0].lower()
    
    print(f"Protein dosyası yükleniyor: {os.path.basename(protein_file)}")
    cmd.load(protein_file, "protein")
    
    # Ligandları yükle (1'den 5'e kadar) - orijinal konformasyonda
    ligand_files = [f for f in os.listdir(ligands_dir) if f.endswith(".pdb")]
    ligand_files.sort()  # Dosyaları sırala
    
    # Debug bilgisi: Ligand dosyalarını listele
    if DEBUG_MODE:
        print(f"\nLigand klasöründeki tüm dosyalar: {', '.join(os.listdir(ligands_dir))}")
        print(f"Filtrelenen .pdb dosyaları: {', '.join(ligand_files)}")
    
    # En fazla 5 ligand kullan
    if len(ligand_files) > 5:
        print(f"Not: 5'ten fazla ligand bulundu, sadece ilk 5 tanesi kullanılacak")
        ligand_files = ligand_files[:5]
    
    if not ligand_files:
        print("Hata: Ligands klasöründe PDB dosyası bulunamadı!")
        return False
    
    print(f"Bulunan ligand dosyaları: {', '.join(ligand_files)}")
    
    # Ligandların konumlarını ve ağırlık merkezlerini saklamak için liste
    ligand_centers = []
    loaded_ligands = []  # Başarıyla yüklenen ligandların listesi
    
    # ZINC ID'yi çıkar (ilk ligand dosyasından)
    first_ligand_name = os.path.basename(ligand_files[0])
    zinc_id = "unknown"
    if "ZINC" in first_ligand_name:
        # ZINC ID'yi bul ve son 4 hanesi al
        zinc_parts = first_ligand_name.split("ZINC")
        if len(zinc_parts) > 1:
            zinc_id_full = "ZINC" + zinc_parts[1].split("_")[0]
            zinc_id = zinc_id_full[-4:] if len(zinc_id_full) >= 4 else zinc_id_full
    
    # Ligandları yükle
    for i, ligand_file in enumerate(ligand_files):
        ligand_name = str(i+1)  # 1, 2, 3, 4, 5 şeklinde isimlendirme
        ligand_path = os.path.join(ligands_dir, ligand_file)
        
        try:
            cmd.load(ligand_path, ligand_name)
            print(f"  * Ligand {i+1} yüklendi: {ligand_file}")
            
            # Ligand ağırlık merkezini hesapla
            com = cmd.centerofmass(ligand_name)
            ligand_centers.append(com)
            loaded_ligands.append(ligand_name)
        except Exception as e:
            print(f"  * Hata: Ligand {ligand_file} yüklenemedi: {str(e)}")
    
    if not loaded_ligands:
        print("Hata: Hiçbir ligand başarıyla yüklenemedi!")
        return False
    
    # ----- RENKLER.PY'DEN ALINAN RENK AYARLARI -----
    
    # Tüm görünümleri gizle önce
    cmd.hide("everything")
    
    # Protein görünümünü ayarla
    cmd.show("cartoon", "protein")
    cmd.show("surface", "protein")
    cmd.set("transparency", 0.5, "protein")
    
    # Ligandları stick olarak göster - her bir ligandı tek tek ayarla
    ligand_selector = " or ".join(loaded_ligands)
    if ligand_selector:
        print(f"Gösterilecek ligandlar: {ligand_selector}")
        cmd.show("sticks", ligand_selector)
    
    # Renk ayarları (renkler.py'den)
    cmd.color("hydrogen")                # Hidrojen atomları
    cmd.color("red", "1")                # 1. ligand: kırmızı
    cmd.color("green", "2")              # 2. ligand: yeşil
    cmd.color("blue", "3")               # 3. ligand: mavi
    cmd.color("purple", "4")             # 4. ligand: mor
    cmd.color("orange", "5")             # 5. ligand: turuncu
    cmd.color("bluewhite", "protein")    # Protein: açık mavi
    
    # Genel görünüm ayarları
    cmd.bg_color("white")                # Arka plan: beyaz
    cmd.space("cmyk")                    # Renk uzayı: cmyk
    
    # Render ayarlarını optimize et
    cmd.set("ray_trace_mode", 0)         # Kaliteli render modu
    cmd.set("ray_shadows", 0)            # Gölgeleri kapat (hızlandırmak için)
    cmd.set("ray_opaque_background", 0)  # Opak arka plan
    cmd.set("depth_cue", 0)              # Derinlik efektini kapat
    
    # ----- DAHA HASSAS PADDİNG HESAPLAMA (REVİZE EDİLMİŞ) -----
    
    # Protein boyutlarını hesapla
    min_xyz_protein, max_xyz_protein = cmd.get_extent("protein")
    
    # En uzak iki nokta arasındaki mesafeyi hesapla (köşegen)
    protein_diagonal = math.sqrt(
        (max_xyz_protein[0] - min_xyz_protein[0])**2 +
        (max_xyz_protein[1] - min_xyz_protein[1])**2 +
        (max_xyz_protein[2] - min_xyz_protein[2])**2
    )
    
    # Ligand boyutlarını hesapla (tüm ligandları birlikte değerlendir)
    if loaded_ligands:
        # Hepsini birden seç
        ligand_selector = " or ".join(loaded_ligands)
        min_xyz_ligand, max_xyz_ligand = cmd.get_extent(ligand_selector)
        
        # Ligandların en uzak iki noktası arasındaki mesafe
        ligand_diagonal = math.sqrt(
            (max_xyz_ligand[0] - min_xyz_ligand[0])**2 +
            (max_xyz_ligand[1] - min_xyz_ligand[1])**2 +
            (max_xyz_ligand[2] - min_xyz_ligand[2])**2
        )
    else:
        ligand_diagonal = 0
    
    # Padding değerlerini belirle (sabit değer veya otomatik hesaplama)
    if FIXED_FAR_PADDING > 0:
        far_padding = FIXED_FAR_PADDING
    else:
        far_padding = protein_diagonal * FAR_PADDING_FACTOR
    
    if FIXED_CLOSE_PADDING > 0:
        close_padding = FIXED_CLOSE_PADDING
    else:
        close_padding = ligand_diagonal * CLOSE_PADDING_FACTOR
    
    print(f"\nHesaplanan değerler:")
    print(f"  * Protein köşegen boyutu: {protein_diagonal:.2f} Å")
    print(f"  * Ligand köşegen boyutu: {ligand_diagonal:.2f} Å")
    print(f"  * Hesaplanan uzak görünüm payı: {far_padding:.2f} Å")
    print(f"  * Hesaplanan yakın görünüm payı: {close_padding:.2f} Å")
    
    # ----- YENİDEN DÜZENLENEN POZ MEKANİZMASI -----
    
    # Ortalama ligand merkezini hesapla
    if ligand_centers:
        avg_ligand_center = [
            sum(center[0] for center in ligand_centers) / len(ligand_centers),
            sum(center[1] for center in ligand_centers) / len(ligand_centers),
            sum(center[2] for center in ligand_centers) / len(ligand_centers)
        ]
    else:
        print("Ligand bulunamadı!")
        return False
    
    # Protein merkezini hesapla
    protein_center = cmd.centerofmass("protein")
    
    # Vektör bileşenlerini hesapla (protein - ligand)
    dx = protein_center[0] - avg_ligand_center[0]
    dy = protein_center[1] - avg_ligand_center[1]
    dz = protein_center[2] - avg_ligand_center[2]
    
    # İki merkez arasındaki mesafeyi hesapla
    distance = math.sqrt(dx*dx + dy*dy + dz*dz)
    
    # Ağırlık merkezlerini ve vektörü göster
    if SHOW_CENTERS:
        # Protein ve ligand ağırlık merkezlerini göster
        print("\nAğırlık merkezleri gösteriliyor...")
        
        # Protein ağırlık merkezi (kırmızı küre)
        cmd.pseudoatom("protein_center", pos=protein_center, color="red", 
                      label="Protein CM", vdw=CENTER_SPHERE_SIZE)
        cmd.show("spheres", "protein_center")
        
        # Ligand ağırlık merkezi (kırmızı küre)
        cmd.pseudoatom("ligand_center", pos=avg_ligand_center, color="red", 
                      label="Ligand CM", vdw=CENTER_SPHERE_SIZE)
        cmd.show("spheres", "ligand_center")
        
        # İki merkez arasında vektör çiz (yeşil silindir)
        cmd.distance("cm_vector", "protein_center", "ligand_center")
        cmd.hide("labels", "cm_vector")
        cmd.set("dash_radius", VECTOR_CYLINDER_RADIUS, "cm_vector")
        cmd.set("dash_color", "green", "cm_vector")
        cmd.set("dash_gap", 0, "cm_vector")  # Sürekli çizgi
        
        print(f"  * Protein ağırlık merkezi: ({protein_center[0]:.2f}, {protein_center[1]:.2f}, {protein_center[2]:.2f})")
        print(f"  * Ligand ağırlık merkezi: ({avg_ligand_center[0]:.2f}, {avg_ligand_center[1]:.2f}, {avg_ligand_center[2]:.2f})")
        print(f"  * Merkezler arası mesafe: {distance:.2f} Å")
        print(f"  * Bakış vektörü: ({dx:.2f}, {dy:.2f}, {dz:.2f})")
    
    # ----- BAĞLANTI VEKTÖRÜ BOYUNCA BAKIŞ AYARLA -----
    # Vektörü normalize et
    length = math.sqrt(dx*dx + dy*dy + dz*dz)
    if length > 0:
        dx /= length
        dy /= length
        dz /= length
    
    # ----- 1) UZAK POZ (FAR VIEW) OLUŞTUR -----
    print("\nUzak görünüm (protein odaklı) oluşturuluyor...")
    
    # Tüm seçimleri ve görünümleri yeniden ayarla
    cmd.hide("everything")
    cmd.show("cartoon", "protein")
    cmd.show("surface", "protein")
    cmd.set("transparency", 0.5, "protein")
    
    if loaded_ligands:
        # Her bir ligandı tek tek ayarla
        cmd.show("sticks", ligand_selector)
    
    # Eğer ağırlık merkezlerini göster seçeneği aktifse, onları da göster
    if SHOW_CENTERS:
        cmd.show("spheres", "protein_center")
        cmd.show("spheres", "ligand_center")
        cmd.show("dashes", "cm_vector")
    
    # Görünümü sıfırla ve ligand ile proteinin tamamını görünür hale getir
    cmd.reset()
    cmd.zoom("all")
    
    # Daha basit bir yaklaşımla bakış açısını ayarlama
    # 1. Merkezleri ayarla
    cmd.center("all")
    
    # 2. Kamerayı ligand merkezine yerleştir
    cmd.origin(position=avg_ligand_center)
    
    # 3. Basit bir rotasyon yaklaşımı kullan
    # Önce düz bakacak şekilde ayarla (varsayılan bakış)
    cmd.reset()
    
    # Görünümü protein merkezinden ligand merkezine bakacak şekilde ayarla
    # Y ve X eksenlerinde gerekli rotasyonları uygula
    
    # Vektör yönünde bakış açısı hesaplama - daha basit yaklaşım
    # Düşey düzlemdeki açı (x-rotasyon)
    x_angle = math.degrees(math.atan2(dy, math.sqrt(dx*dx + dz*dz)))
    
    # Yatay düzlemdeki açı (y-rotasyon)
    y_angle = math.degrees(math.atan2(dx, dz))
    
    # Açıları uygula
    cmd.turn("y", -y_angle)  # Sağa/sola dönüş (yatay)
    cmd.turn("x", x_angle)   # Yukarı/aşağı dönüş (dikey)
    
    # Tam tersi açıdan bakmamız gerekiyor (ligand→protein yönünde)
    cmd.turn("y", 180)
    
    # Z ekseni etrafında döndürerek ligandı dik pozisyona getir
    cmd.turn("z", Z_ROTATION_ANGLE)
    
    # Proteinin en uzak noktalarına göre görünümü ayarla
    # Protein ve yakın çevresini içine alacak bir görünüm
    cmd.zoom("protein", far_padding)
    
    # Bakış açısı bilgisini al ve göster
    if DEBUG_MODE:
        current_view = cmd.get_view()
        print(f"  * Bakış matrisi: {current_view}")
    
    # Yeni dosya isimlendirme formatı: pdbid_zincidson4hanesi_far.png
    far_base_name = f"{pdb_id}_{zinc_id}_far"
    
    # Uzak görünüm için PyMOL oturumunu kaydet
    far_session_file = os.path.join(output_dir, f"{far_base_name}.pse")
    cmd.save(far_session_file)
    
    # Uzak görünümün PNG görselini oluştur
    far_image_file = os.path.join(output_dir, f"{far_base_name}.png")
    
    # Ray-traced render ile görsel oluştur
    print(f"Uzak görünüm render ediliyor... ({IMAGE_WIDTH}x{IMAGE_HEIGHT}, {IMAGE_DPI} dpi)")
    cmd.png(far_image_file, width=IMAGE_WIDTH, height=IMAGE_HEIGHT, dpi=IMAGE_DPI, ray=1)
    
    # ----- 2) YAKIN POZ (CLOSE VIEW) OLUŞTUR -----
    print("\nYakın görünüm (ligand odaklı) oluşturuluyor...")
    
    # Tüm seçimleri ve görünümleri yeniden ayarla
    cmd.hide("everything")
    cmd.show("cartoon", "protein")
    cmd.show("surface", "protein")
    cmd.set("transparency", 0.5, "protein")
    
    if loaded_ligands:
        # Her bir ligandı tek tek ayarla
        cmd.show("sticks", ligand_selector)
    
    # Eğer ağırlık merkezlerini göster seçeneği aktifse, onları da göster
    if SHOW_CENTERS:
        cmd.show("spheres", "protein_center")
        cmd.show("spheres", "ligand_center")
        cmd.show("dashes", "cm_vector")
    
    # Mevcut bakış açısını koru, sadece yakınlaştırma seviyesini değiştir
    # Bu sayede her iki görünümde de aynı açıdan bakacağız
    
    # Sadece ligandlara odaklan
    if loaded_ligands:
        cmd.zoom(ligand_selector, close_padding)
    
    # Yeni dosya isimlendirme formatı: pdbid_zincidson4hanesi_close.png
    close_base_name = f"{pdb_id}_{zinc_id}_close"
    
    # Yakın görünüm için PyMOL oturumunu kaydet
    cmd.clip('far', -300)
    cmd.clip('near', 300)
    close_session_file = os.path.join(output_dir, f"{close_base_name}.pse")
    cmd.save(close_session_file)
    
    # Yakın görünümün PNG görselini oluştur
    close_image_file = os.path.join(output_dir, f"{close_base_name}.png")
    
    # Ray-traced render ile görsel oluştur
    print(f"Yakın görünüm render ediliyor... ({IMAGE_WIDTH}x{IMAGE_HEIGHT}, {IMAGE_DPI} dpi)")
    cmd.png(close_image_file, width=IMAGE_WIDTH, height=IMAGE_HEIGHT, dpi=IMAGE_DPI, ray=1)
    
    print(f"\nİşlem tamamlandı. İki farklı görünüm kaydedildi:")
    print(f"  * Uzak görünüm: {far_session_file}")
    print(f"  * Uzak görünüm görseli: {far_image_file}")
    print(f"  * Yakın görünüm: {close_session_file}")
    print(f"  * Yakın görünüm görseli: {close_image_file}")
    
    # Yüklenen ligand sayısını göster
    print(f"  * Toplam {len(loaded_ligands)}/{len(ligand_files)} ligand başarıyla yüklendi ve görselleştirildi")
    
    print("\nUygulanan ayarlar:")
    print("  * bg_color white")
    print("  * space cmyk")
    print("  * Protein: bluewhite (cartoon ve surface)")
    print("  * Ligand 1: kırmızı")
    print("  * Ligand 2: yeşil")
    print("  * Ligand 3: mavi")
    print("  * Ligand 4: mor")
    print("  * Ligand 5: turuncu")
    
    if SHOW_CENTERS:
        print("  * Protein ve ligand ağırlık merkezleri: kırmızı küreler")
        print("  * Merkezler arası vektör: yeşil çizgi")
    
    print(f"  * Ligand dik pozisyon için z-rotasyon: {Z_ROTATION_ANGLE} derece")
    
    print("\nRender özellikleri:")
    print(f"  * Çözünürlük: {IMAGE_DPI} dpi")
    print(f"  * Görsel boyutu: {IMAGE_WIDTH}x{IMAGE_HEIGHT} piksel")
    print("  * Ray-traced kaliteli render")
    
    return True

def main():
    # Komut satırı argümanlarını işle
    parser = argparse.ArgumentParser(description='Protein-ligand görselleştirmesi oluşturur.')
    parser.add_argument('--pdb_id', type=str, help='Protein yapısının PDB ID\'si')
    parser.add_argument('--ligands_dir', type=str, help='Ligand dosyalarının bulunduğu klasör yolu')
    parser.add_argument('--output_dir', type=str, default='results', help='Çıktı dosyalarının kaydedileceği klasör')
    parser.add_argument('--width', type=int, default=IMAGE_WIDTH, help='Görsel genişliği (piksel)')
    parser.add_argument('--height', type=int, default=IMAGE_HEIGHT, help='Görsel yüksekliği (piksel)')
    parser.add_argument('--dpi', type=int, default=IMAGE_DPI, help='Görsel çözünürlüğü (DPI)')
    
    args = parser.parse_args()
    
    # Görselleştirme fonksiyonunu çağır
    success = final_gorselleştirme(
        pdb_id=args.pdb_id,
        ligands_dir=args.ligands_dir,
        output_dir=args.output_dir,
        image_width=args.width,
        image_height=args.height,
        image_dpi=args.dpi
    )
    
    # Başarı durumuna göre çıkış kodu döndür
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 
