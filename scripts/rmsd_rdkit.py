from CalcLigRMSD import *
import os
from rdkit import Chem

# Klasördeki tüm pdb dosyalarını listele
pdb_files = [f for f in os.listdir('.') if f.endswith('.pdb') and f != 'ligand.pdb']

# Kristal ligandı yükle
crystal_ligand = Chem.MolFromPDBFile("ligand.pdb")

# RMSD sonuçlarını saklamak için bir liste oluştur
rmsd_results = []

for pdb_file in pdb_files:
    # Docked ligandı yükle
    docked_ligand = Chem.MolFromPDBFile(pdb_file)

    # RMSD hesapla ve sonucu kaydet
    rmsd = CalcLigRMSD(docked_ligand, crystal_ligand, rename_lig2=True, output_filename=f"{pdb_file}_result.pdb")
    
    # Sonucu listeye ekle
    rmsd_results.append((pdb_file, rmsd))

# Sonuçları bir dosyaya yaz
with open('rmsd_results.txt', 'w') as f:
    f.write("Result Name\tRMSD\n")
    for result in rmsd_results:
        f.write(f"{result[0]}\t{result[1]:.2f}\n")
