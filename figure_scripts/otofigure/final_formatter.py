#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import gridspec
from matplotlib.gridspec import GridSpec
from matplotlib.image import imread
import re
import matplotlib.patches as patches
from collections import defaultdict
import matplotlib.font_manager as fm
import argparse
import sys
from pathlib import Path

# Global stil ve görsel parametreleri
RENDER_DPI = 300  # Çıkış görüntüsünün DPI değeri
FIGURE_NUMBERS = [10, 10]  # Her figürde kaç satır (protein) olacağı
FONT_SIZE_TITLE = 14  # Başlık yazı boyutu
FONT_SIZE_AXES = 12  # Eksen yazı boyutu
FONT_SIZE_LABELS = 12  # Etiket yazı boyutu (büyütüldü)
FONT_SIZE_OXA = 14  # OXA yazı boyutu (yeni eklendi)
FIGURE_WIDTH = 16  # inç cinsinden figür genişliği
FIGURE_HEIGHT = 20  # inç cinsinden figür yüksekliği
OUTPUT_DIR = "formatted_results"  # Çıkış klasörü (değiştirildi: Formatted_Results -> formatted_results)
OXA_CSV_PATH = "oxa_pdb_list.csv"  # OXA-PDB eşleşme listesi CSV dosyası

# Padding ayarları - ayarlanabilir parametreler
OUTER_PADDING = 0.0  # Figür dışındaki boşluk miktarı (inç)
OXA_LABEL_PADDING = 0.0  # OXA yazıları etrafındaki padding (yüzde olarak, 0-1 arası)
ZINC_LABEL_PADDING = 0.0  # ZINC ID yazıları etrafındaki padding (yüzde olarak, 0-1 arası)
SUBPLOT_WSPACE = 0.0  # Alt grafikler arasındaki yatay boşluk
SUBPLOT_HSPACE = 0.0  # Alt grafikler arasındaki dikey boşluk

# Varsayılan değerler
DEFAULT_OUTPUT_DIR = "formatted_results"  # Formatlanmış görsel çıktı klasörü
DEFAULT_OXA_PDB_CSV = "oxa_pdb_list.csv"  # OXA-PDB eşleştirme dosyası
DEFAULT_ZINC_CSV = "ZINC.csv"  # ZINC-ID eşleştirme dosyası
DEFAULT_MAX_IMAGES = 16  # Her sayfada maksimum resim sayısı
DEFAULT_RENDER_DPI = 300  # Görüntü oluşturma DPI değeri

def load_oxa_pdb_mapping(file_path=DEFAULT_OXA_PDB_CSV):
    """
    OXA-PDB eşleştirmelerini yükler.
    
    Args:
        file_path (str): CSV dosya yolu
        
    Returns:
        dict: PDB ID'den OXA adına eşleştirme sözlüğü
    """
    mapping = {}
    
    # Dosya varsa yükle
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path)
            # CSV formatına göre sütun adlarını belirle
            if 'PDB_ID' in df.columns and 'OXA' in df.columns:
                for _, row in df.iterrows():
                    mapping[row['PDB_ID'].lower()] = row['OXA']
            else:
                print(f"Uyarı: OXA-PDB eşleştirme dosyası ({file_path}) beklenen formatta değil.")
                print("Beklenen sütunlar: 'PDB_ID', 'OXA'")
        except Exception as e:
            print(f"Uyarı: OXA-PDB eşleştirme dosyası okunamadı: {e}")
    else:
        print(f"Uyarı: OXA-PDB eşleştirme dosyası bulunamadı: {file_path}")
        print("PDB ID'ler doğrudan kullanılacak.")
    
    return mapping

def load_zinc_mapping(file_path=DEFAULT_ZINC_CSV):
    """
    ZINC ID eşleştirmelerini yükler. 
    4 haneli ZINC ID'den tam ZINC ID'ye eşleştirme yapar.
    
    Args:
        file_path (str): CSV dosya yolu
        
    Returns:
        dict: 4 haneli ZINC ID'den tam ZINC ID'ye eşleştirme sözlüğü
    """
    mapping = {}
    
    # Dosya varsa yükle
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path)
            # CSV formatına göre sütun adlarını belirle
            if 'Short_ID' in df.columns and 'Full_ID' in df.columns:
                for _, row in df.iterrows():
                    mapping[str(row['Short_ID'])] = row['Full_ID']
            else:
                print(f"Uyarı: ZINC eşleştirme dosyası ({file_path}) beklenen formatta değil.")
                print("Beklenen sütunlar: 'Short_ID', 'Full_ID'")
        except Exception as e:
            print(f"Uyarı: ZINC eşleştirme dosyası okunamadı: {e}")
    else:
        print(f"Uyarı: ZINC eşleştirme dosyası bulunamadı: {file_path}")
        print("4 haneli ZINC ID'ler doğrudan kullanılacak.")
    
    return mapping

def process_image_directory(input_dir, oxa_pdb_mapping=None, zinc_mapping=None):
    """
    Belirtilen klasördeki final görüntü dosyalarını işler
    
    Args:
        input_dir (str): PNG dosyalarının bulunduğu klasör
        oxa_pdb_mapping (dict): PDB ID'den OXA adına eşleştirme sözlüğü
        zinc_mapping (dict): 4 haneli ZINC ID'den tam ZINC ID'ye eşleştirme sözlüğü
        
    Returns:
        pandas.DataFrame: İşlenmiş veri çerçevesi
    """
    # Klasördeki tüm final PNG dosyalarını bul
    final_images = glob.glob(os.path.join(input_dir, "*_final.png"))
    
    if not final_images:
        print(f"Hata: {input_dir} klasöründe final görüntü dosyası bulunamadı!")
        return None
    
    # DataFrame için veri listesi
    data_list = []
    
    # Her görüntü için
    for image_path in final_images:
        filename = os.path.basename(image_path)
        parts = filename.split('_')
        
        if len(parts) < 3:
            print(f"Atlanıyor: {filename} - Dosya adı uygun formatta değil")
            continue
        
        pdb_id = parts[0].lower()
        zinc_id = parts[1]
        
        # Tam ZINC ID'yi belirle
        full_zinc_id = None
        if zinc_mapping and zinc_id in zinc_mapping:
            full_zinc_id = zinc_mapping[zinc_id]
        else:
            # Eşleştirme yoksa, varsayılan olarak "ZINC" + ID kullan
            full_zinc_id = f"ZINC{zinc_id}"
        
        # OXA eşleştirmesini belirle
        oxa = oxa_pdb_mapping.get(pdb_id, pdb_id) if oxa_pdb_mapping else pdb_id
        
        # Veri listesine ekle
        data_list.append({
            'pdb_id': pdb_id,
            'zinc_id': zinc_id,
            'full_zinc_id': full_zinc_id,
            'oxa': oxa,
            'output_file': image_path
        })
    
    if not data_list:
        print("Hata: İşlenecek dosya bulunamadı.")
        return None
    
    # DataFrame oluştur
    df = pd.DataFrame(data_list)
    print(f"Toplam {len(df)} işlenecek dosya bulundu.")
    
    return df

def create_formatted_figures(df, output_dir=DEFAULT_OUTPUT_DIR, max_images=DEFAULT_MAX_IMAGES, render_dpi=DEFAULT_RENDER_DPI):
    """
    İşlenmiş verilerden formatlanmış figürler oluşturur.
    
    Args:
        df (pandas.DataFrame): İşlenmiş veri çerçevesi
        output_dir (str): Çıktı klasörü
        max_images (int): Her sayfada maksimum resim sayısı
        render_dpi (int): Görüntü oluşturma DPI değeri
        
    Returns:
        list: Oluşturulan dosyaların listesi
    """
    # Çıktı klasörünü oluştur
    os.makedirs(output_dir, exist_ok=True)
    
    # Boş veri kontrolü
    if df is None or len(df) == 0:
        print("Hata: İşlenecek veri bulunamadı.")
        return []
    
    # Sonuçları OXA ve ZINC ID'ye göre grupla
    grouped = df.groupby(['oxa', 'full_zinc_id'])
    
    # Her sayfada kaç resim olacağını belirle
    images_per_page = min(max_images, len(grouped))
    rows = int(np.ceil(np.sqrt(images_per_page)))
    cols = int(np.ceil(images_per_page / rows))
    
    # Grupları sayfalar halinde dağıt
    groups = list(grouped.groups.items())
    num_pages = int(np.ceil(len(groups) / images_per_page))
    
    output_files = []
    
    # Her sayfa için
    for page in range(num_pages):
        # Bu sayfadaki grupları belirle
        start_idx = page * images_per_page
        end_idx = min((page + 1) * images_per_page, len(groups))
        page_groups = groups[start_idx:end_idx]
        
        # Sayfada kaç resim olacağını belirle
        actual_images = len(page_groups)
        actual_rows = int(np.ceil(np.sqrt(actual_images)))
        actual_cols = int(np.ceil(actual_images / actual_rows))
        
        # Figür oluştur
        fig = plt.figure(figsize=(5*actual_cols, 5*actual_rows), dpi=render_dpi)
        fig.patch.set_alpha(0)
        gs = GridSpec(actual_rows, actual_cols, figure=fig)
        
        # Her grup için bir alt figür oluştur
        for i, ((oxa, zinc_id), group_indices) in enumerate(page_groups):
            # Grup için indeksleri al
            indices = grouped.groups[(oxa, zinc_id)]
            
            # Dosya yolunu al
            if len(indices) > 0:
                row = df.iloc[indices[0]]
                image_path = row['output_file']
                
                # Görüntüyü oku
                if os.path.exists(image_path):
                    # Alt figür oluştur
                    row_idx = i // actual_cols
                    col_idx = i % actual_cols
                    ax = fig.add_subplot(gs[row_idx, col_idx])
                    ax.set_facecolor((1, 1, 1, 0))
                    ax.patch.set_alpha(0)
                    
                    # Görüntüyü yükle
                    try:
                        img = plt.imread(image_path)
                        ax.imshow(img)
                        ax.set_title(f"{oxa} - {zinc_id}")
                        ax.axis('off')
                    except Exception as e:
                        print(f"Uyarı: Görüntü yüklenemedi {image_path}: {e}")
                        ax.text(0.5, 0.5, f"Görüntü yüklenemedi:\n{image_path}", 
                                ha='center', va='center', transform=ax.transAxes)
                        ax.axis('off')
                else:
                    print(f"Uyarı: Görüntü dosyası bulunamadı: {image_path}")
        
        # Boşlukları ayarla
        plt.tight_layout()
        
        # Dosyayı kaydet
        output_file = os.path.join(output_dir, f"formatted_figure_{page+1}.png")
        plt.savefig(output_file, dpi=render_dpi, bbox_inches='tight', transparent=True)
        plt.close(fig)
        
        print(f"Formatlanmış görüntü oluşturuldu: {output_file}")
        output_files.append(output_file)
    
    return output_files

def main():
    """
    Ana fonksiyon: Komut satırı argümanlarını işler ve analiz yapar.
    """
    parser = argparse.ArgumentParser(description='Final görüntülerden formatlanmış görselleştirmeler oluşturur.')
    parser.add_argument('--input_dir', type=str, default="final_results", 
                        help='Final görüntülerin bulunduğu klasör')
    parser.add_argument('--output_dir', type=str, default=DEFAULT_OUTPUT_DIR, 
                        help='Çıktı klasörü')
    parser.add_argument('--oxa_pdb_csv', type=str, default=DEFAULT_OXA_PDB_CSV, 
                        help='OXA-PDB eşleştirme dosyası')
    parser.add_argument('--zinc_csv', type=str, default=DEFAULT_ZINC_CSV, 
                        help='ZINC ID eşleştirme dosyası')
    parser.add_argument('--max_images', type=int, default=DEFAULT_MAX_IMAGES, 
                        help='Her sayfada maksimum resim sayısı')
    parser.add_argument('--render_dpi', type=int, default=DEFAULT_RENDER_DPI, 
                        help='Görüntü oluşturma DPI değeri')
    parser.add_argument('--debug', action='store_true', 
                        help='Debug modunu etkinleştir')
    
    args = parser.parse_args()
    
    # Debug modunda tüm parametreleri göster
    if args.debug:
        print("Çalıştırma ayarları:")
        print(f"  * Giriş klasörü: {args.input_dir}")
        print(f"  * Çıktı klasörü: {args.output_dir}")
        print(f"  * OXA-PDB eşleştirme dosyası: {args.oxa_pdb_csv}")
        print(f"  * ZINC ID eşleştirme dosyası: {args.zinc_csv}")
        print(f"  * Her sayfada maksimum resim: {args.max_images}")
        print(f"  * Görüntü oluşturma DPI: {args.render_dpi}")
    
    # OXA-PDB eşleştirmelerini yükle
    oxa_pdb_mapping = load_oxa_pdb_mapping(args.oxa_pdb_csv)
    
    # ZINC ID eşleştirmelerini yükle
    zinc_mapping = load_zinc_mapping(args.zinc_csv)
    
    # Görüntü klasörünü işle
    df = process_image_directory(args.input_dir, oxa_pdb_mapping, zinc_mapping)
    
    if df is None:
        return 1
    
    # Formatlanmış figürleri oluştur
    output_files = create_formatted_figures(
        df, args.output_dir, args.max_images, args.render_dpi
    )
    
    if not output_files:
        print("Hata: Formatlanmış figürler oluşturulamadı.")
        return 1
    
    print(f"Toplam {len(output_files)} formatlanmış figür oluşturuldu.")
    return 0

if __name__ == "__main__":
    sys.exit(main()) 
