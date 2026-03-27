#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import subprocess
import sys

def main():
    # Klasörleri oluştur
    os.makedirs("results", exist_ok=True)
    os.makedirs("final_results", exist_ok=True)
    os.makedirs("formatted_results", exist_ok=True)
    
    print("1. Final dinamik adımı çalıştırılıyor...")
    
    # Protein klasöründeki ilk PDB dosyasını bul
    protein_files = glob.glob("protein/*.pdb")
    if not protein_files:
        print("Hata: Protein klasöründe PDB dosyası bulunamadı!")
        return 1
    
    protein_file = protein_files[0]
    pdb_id = os.path.splitext(os.path.basename(protein_file))[0].lower()
    
    # Data klasöründeki tüm ligand klasörlerini bul
    ligand_dirs = []
    for item in os.listdir("data"):
        item_path = os.path.join("data", item)
        if os.path.isdir(item_path) and glob.glob(os.path.join(item_path, "*.pdb")):
            ligand_dirs.append(item_path)
    
    if not ligand_dirs:
        print("Hata: Data klasöründe ligand klasörü bulunamadı!")
        return 1
    
    # Her ligand klasörü için final_dinamik.py çalıştır
    success_count = 0
    fail_count = 0
    
    for i, ligand_dir in enumerate(ligand_dirs):
        print(f"  İşleniyor: Kombinasyon {i+1}/{len(ligand_dirs)} - Ligand klasörü: {ligand_dir}")
        
        cmd = [
            "python", "final_dinamik.py",
            "--pdb_id", pdb_id,
            "--ligands_dir", ligand_dir,
            "--output_dir", "results"
        ]
        
        try:
            subprocess.run(cmd, check=True)
            success_count += 1
            print(f"  ✓ Başarılı: {pdb_id} + {os.path.basename(ligand_dir)}")
        except subprocess.CalledProcessError as e:
            fail_count += 1
            print(f"  ✗ Başarısız: {pdb_id} + {os.path.basename(ligand_dir)}")
    
    print(f"\nDinamik işlem tamamlandı. Başarılı: {success_count}, Başarısız: {fail_count}")
    
    # 2. Görselleştirme adımı
    print("\n2. Final görselleştirme oluşturuluyor...")
    
    try:
        cmd = [
            "python", "create_visualization.py",
            "--input_dir", "results",
            "--output_dir", "final_results",
            "--interaction_dir", "interaction"
        ]
        
        subprocess.run(cmd, check=True)
        print("✓ Final görselleştirme başarıyla oluşturuldu")
    except subprocess.CalledProcessError as e:
        print("✗ Final görselleştirme oluşturulurken bir hata oluştu!")
        return 1
    
    # 3. Final formatter adımı
    print("\n3. Final formatter çalıştırılıyor...")
    
    # Final results klasöründeki görüntüleri doğrudan kullan
    try:
        cmd = [
            "python", "final_formatter.py",
            "--input_dir", "final_results",
            "--output_dir", "formatted_results"
        ]
        
        subprocess.run(cmd, check=True)
        print("✓ Final formatter başarıyla çalıştırıldı")
    except subprocess.CalledProcessError as e:
        print("✗ Final formatter çalıştırılırken bir hata oluştu!")
        return 1
    
    print("\nTüm işlemler tamamlandı.")
    return 0

if __name__ == "__main__":
    sys.exit(main()) 