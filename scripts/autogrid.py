import pymol
import pymol.cmd as cmd
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import sys
import argparse
import os

def extract_raw_text_pdbsum(pdb_id, ligand_name=None):
    """
    PDBsum web sitesinden bir proteinin ligand verilerini web scraping ile çeker.
    Eğer ligand_name verilmişse, o ligandın verilerini çekmeye çalışır.
    Verilmemişse ilk ligandın verilerini çeker.
    """
    print(f"[DEBUG] extract_raw_text_pdbsum çağrıldı. PDB ID: {pdb_id}, İstenen Ligand: {ligand_name}")

    if ligand_name:
        found_url = None
        available_ligands_on_site = set()

        for ligtype_val in range(1, 15): # Geniş bir aralık deniyoruz
            current_url = f"https://www.ebi.ac.uk/thornton-srv/databases/cgi-bin/pdbsum/GetLigInt.pl?pdb={pdb_id}&ligtype={ligtype_val:02d}&ligno=01"
            print(f"[DEBUG] Denenen URL: {current_url}")
            try:
                response = requests.get(current_url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')
                page_text = soup.get_text(separator='\n') # strip=True kaldırıldı
                
                # Ligand adını daha esnek bir regex ile ara
                match = re.search(r"Ligand\s+([A-Z0-9]{3,})", page_text)
                if match:
                    current_ligand_resn = match.group(1).strip()
                    available_ligands_on_site.add(current_ligand_resn)
                    print(f"[DEBUG] Bulunan Ligand ({current_url}): {current_ligand_resn}")

                    if current_ligand_resn.upper() == ligand_name.upper():
                        found_url = current_url
                        print(f"[DEBUG] İstenen Ligand '{ligand_name}' bulundu. Kullanılacak URL: {found_url}")
                        break
            except requests.exceptions.RequestException as e:
                print(f"[DEBUG] URL erişim hatası ({current_url}): {e}")
                continue
            except Exception as e:
                print(f"[DEBUG] Beklenmeyen hata ({current_url}): {e}")
                continue
        
        if found_url:
            response = requests.get(found_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            page_text = soup.get_text(separator='\n') # strip=True kaldırıldı
            print(f"""[DEBUG] Çekilen ham metin (ilk 500 karakter):
{page_text[:500]}""")
            return page_text
        else:
            if available_ligands_on_site:
                raise Exception(f"Ligand '{ligand_name}' PDBsum'da bulunamadı. Mevcut ligandlar: {', '.join(sorted(list(available_ligands_on_site)))}")
            else:
                raise Exception(f"Ligand '{ligand_name}' PDBsum'da bulunamadı ve mevcut ligand listesi alınamadı.")
    else:
        url = f"https://www.ebi.ac.uk/thornton-srv/databases/cgi-bin/pdbsum/GetLigInt.pl?pdb={pdb_id}&ligtype=01&ligno=01"
        print(f"[DEBUG] Ligand belirtilmedi. Varsayılan URL: {url}")

        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            page_text = soup.get_text(separator='\n') # strip=True kaldırıldı
            print(f"""[DEBUG] Çekilen ham metin (ilk 500 karakter):
{page_text[:500]}""")
            return page_text
        except requests.exceptions.RequestException as e:
            raise Exception(f"Web sitesine erişilemedi veya istek başarısız oldu ({pdb_id}): {e}")
        except Exception as e:
            raise Exception(f"Bilinmeyen Hata oluştu ({pdb_id}): {e}")

def extract_interaction_data(raw_text, section_name):
    """
    Verilen metin içerisinden ilgili interaksiyon tablosunu çıkarır ve ligand adını bulur.
    """
    print(f"[DEBUG] extract_interaction_data çağrıldı. Section: {section_name}")
    print(f"""[DEBUG] extract_interaction_data - Gelen ham metin (ilk 500 karakter):
{raw_text[:500]}""")

    lines = raw_text.split("\n")
    start_index = None
    ligand_name = None

    # Ligand adını "PDB code: XXXX Ligand YYY" satırından çıkar
    for line in lines:
        if "PDB code:" in line and "Ligand" in line:
            # Ligand adını daha esnek bir regex ile ara
            match = re.search(r"Ligand\s+([A-Z0-9]{3,})", line)
            if match:
                ligand_name = match.group(1).strip()
                print(f"[DEBUG] extract_interaction_data - Sayfadan çıkarılan ligand adı: {ligand_name}")
                break

    for i, line in enumerate(lines):
        if section_name in line:
            start_index = i + 7
            break

    if start_index is None:
        print(f"[DEBUG] extract_interaction_data - '{section_name}' tablosu bulunamadı.")
        return None, ligand_name

    interaction_data = []
    pattern = re.compile(r'^\s*\d+\.\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+([\d\.]+)')

    for line in lines[start_index:]:
        match = pattern.match(line)
        if match:
            interaction_data.append(match.groups())
        elif len(line.strip()) == 0 or "Total" in line:
            break

    if interaction_data:
        df = pd.DataFrame(interaction_data, columns=[
            "Atom1_no", "Atom1_name", "Res1_name", "Res1_no", "Chain1",
            "Atom2_no", "Atom2_name", "Res2_name", "Res2_no", "Chain2", "Distance"
        ])
        print(f"[DEBUG] extract_interaction_data - Etkileşim verisi çekildi. Satır sayısı: {len(df)}")
        return df, ligand_name
    else:
        print(f"[DEBUG] extract_interaction_data - Etkileşim verisi bulunamadı.")
        return None, ligand_name

def setup_pymol_gridbox(pdb_id, output_dir=None, ligand_name=None, chain="A", receptor_path=None, visualize=False):
    """
    PyMOL komut satırında çalıştırılabilecek bir fonksiyon.
    Ligandı merkez alarak, interaksiyon içeren tüm kalıntıları kapsayan en küçük box'ı belirler.
    """
    print(f"[DEBUG] setup_pymol_gridbox çağrıldı. PDB ID: {pdb_id}, İstenen Ligand: {ligand_name}")
    cmd.reinitialize()
    use_chain = chain.lower() != "all"
    if receptor_path:
        print(f"[DEBUG] setup_pymol_gridbox - Yerel reseptör dosyası kullanılacak: {receptor_path}")
        cmd.load(receptor_path, pdb_id)
    else:
        cmd.fetch(pdb_id)
    cmd.hide("everything")
    # temizle
    cmd.remove("solvent")
    cmd.remove("inorganic")

    hydrogen_bonds_df = None
    extracted_ligand_name_hbond = None
    non_bonded_contacts_df = None
    extracted_ligand_name_nonbonded = None

    try:
        # Eğer kullanıcı ligand adı belirtmişse, extract_raw_text_pdbsum'a ilet
        raw_text_content = extract_raw_text_pdbsum(pdb_id, ligand_name)
        hydrogen_bonds_df, extracted_ligand_name_hbond = extract_interaction_data(raw_text_content, "Hydrogen bonds")
        non_bonded_contacts_df, extracted_ligand_name_nonbonded = extract_interaction_data(raw_text_content, "Non-bonded contacts")
    except Exception as e:
        print(f"Uyarı: PDBsum verisi alınamadı veya işlenemedi, ligand etrafında fallback box kullanılacak. Detay: {e}")
        # Devam et, fallback çalışacak

    # Kullanıcının belirttiği ligand adını öncelikli olarak kullan
    # Eğer kullanıcı belirtmediyse veya PDBsum'dan çekilenler farklıysa, birini kullan
    final_ligand_name = None
    if ligand_name:
        final_ligand_name = ligand_name # Kullanıcının belirttiği öncelikli
    elif extracted_ligand_name_hbond: # H-bağlarından gelen ligand
        final_ligand_name = extracted_ligand_name_hbond
    elif extracted_ligand_name_nonbonded: # Non-bonded kontaklardan gelen ligand
        final_ligand_name = extracted_ligand_name_nonbonded
    
    print(f"[DEBUG] setup_pymol_gridbox - Son belirlenen ligand adı: {final_ligand_name}")

    # 1) Ligand'ı ÖNCE seç (zincir duyarlı), sonra temizliği ligand hariç yap
    if final_ligand_name:
        if use_chain:
            cmd.select('lig', f'resn {final_ligand_name} and chain {chain}')
        else:
            cmd.select('lig', f'resn {final_ligand_name}')
        if cmd.count_atoms('lig') == 0:
            cmd.select('lig', f'resn {final_ligand_name}')
    else:
        if use_chain:
            cmd.select('lig', f'organic and chain {chain}')
        else:
            cmd.select('lig', 'organic')
    if cmd.count_atoms('lig') == 0:
        cmd.select('lig', 'organic')
    if cmd.count_atoms('lig') == 0:
        print("Hata: Ligand seçimi yapılamadı (organic/resn). İşlem durduruluyor.")
        return

    # Eğer birden fazla organik parça varsa en büyüğünü tut
    residues = {}
    model = cmd.get_model('lig')
    for a in model.atom:
        resid = (a.resi, a.resn)
        residues[resid] = residues.get(resid, 0) + 1
    if residues:
        resi, resn = max(residues.items(), key=lambda kv: kv[1])[0]
        cmd.select('lig', f'resn {resn} and resi {resi}')

    print(f"[DEBUG] ligand atom sayısı (temizlik öncesi): {cmd.count_atoms('lig')}")

    # 2) Temizlik: ligand hariç solvent/inorganic ve zincir dışı her şeyi kaldır
    cmd.remove('solvent and not lig')
    cmd.remove('inorganic and not lig')
    if use_chain:
        cmd.remove(f'not (chain {chain} or lig)')

    print(f"[DEBUG] ligand atom sayısı (temizlik sonrası): {cmd.count_atoms('lig')}")

    ligand_coords = [atom.coord for atom in cmd.get_model("lig").atom]
    if not ligand_coords:
        print("Hata: Ligand atom koordinatları alınamadı.")
        return

    x = sum(coord[0] for coord in ligand_coords) / len(ligand_coords)
    y = sum(coord[1] for coord in ligand_coords) / len(ligand_coords)
    z = sum(coord[2] for coord in ligand_coords) / len(ligand_coords)

    interacting_residue_numbers = set()

    # 2.1) PDBsum verisi uygunsa, onu dikkate al
    if hydrogen_bonds_df is not None and final_ligand_name:
        if extracted_ligand_name_hbond and extracted_ligand_name_hbond.upper() == final_ligand_name.upper():
            interacting_residue_numbers.update(hydrogen_bonds_df["Res1_no"].astype(int))

    if non_bonded_contacts_df is not None and final_ligand_name:
        if extracted_ligand_name_nonbonded and extracted_ligand_name_nonbonded.upper() == final_ligand_name.upper():
            interacting_residue_numbers.update(non_bonded_contacts_df["Res1_no"].astype(int))

    coords = []
    interacting_residues = set()
    # 2.2) Yerel etkileşim fallback: ligand çevresi 4-6 Å içindeki protein kalıntılarını al
    if not interacting_residue_numbers:
        for cutoff in (4.0, 5.0, 6.0):
            if use_chain:
                sel = f"byres (br. lig around {cutoff}) and polymer and chain {chain}"
            else:
                sel = f"byres (br. lig around {cutoff}) and polymer"
            cmd.select('interacting_res', sel)
            if cmd.count_atoms('interacting_res') > 0:
                print(f"[DEBUG] Yerel etkileşim bulundu (cutoff={cutoff} Å). Atom sayısı: {cmd.count_atoms('interacting_res')}")
                for atom in cmd.get_model('interacting_res').atom:
                    coords.append((atom.coord[0], atom.coord[1], atom.coord[2]))
                    interacting_residues.add((atom.chain.strip() or "_", atom.resi, atom.resn))
                break
        if not coords:
            print("Uyarı: Yakın komşu kalıntı bulunamadı, sadece ligand etrafında sabit kutu (20Å) kullanılacak.")
            x_size = 20.0; y_size = 20.0; z_size = 20.0
    else:
        interacting_residues_str = "+".join(map(str, sorted(list(interacting_residue_numbers))))
        if use_chain:
            interacting_residues_selection = f"resi {interacting_residues_str} and chain {chain}"
        else:
            interacting_residues_selection = f"resi {interacting_residues_str}"
        cmd.select("interacting_res", interacting_residues_selection)
        if cmd.count_atoms("interacting_res") == 0:
            print(f"Uyarı: PDBsum kalıntıları zincir {chain} içinde seçilemedi. Ligand çevresinde sabit kutu (20Å).")
            x_size = 20.0; y_size = 20.0; z_size = 20.0
        else:
            for atom in cmd.get_model("interacting_res").atom:
                coords.append((atom.coord[0], atom.coord[1], atom.coord[2]))
                interacting_residues.add((atom.chain.strip() or "_", atom.resi, atom.resn))

    # 2.3) Kutuyu koordinatlardan hesapla (ligand + etkileşimli kalıntılar)
    if coords:
        allx = [c[0] for c in coords] + [c[0] for c in ligand_coords]
        ally = [c[1] for c in coords] + [c[1] for c in ligand_coords]
        allz = [c[2] for c in coords] + [c[2] for c in ligand_coords]
        minx,maxx = min(allx), max(allx)
        miny,maxy = min(ally), max(ally)
        minz,maxz = min(allz), max(allz)
        cx = (minx+maxx)/2; cy = (miny+maxy)/2; cz = (minz+maxz)/2
        pad = 6.0
        sx = max((maxx-minx)+pad, 20.0)
        sy = max((maxy-miny)+pad, 20.0)
        sz = max((maxz-minz)+pad, 20.0)
        x, y, z = cx, cy, cz
        x_size, y_size, z_size = sx, sy, sz

    # Only perform PyMOL visualization if explicitly requested
    if visualize:
        # Görselleştirme; seçim isimlerini 'lig' ile eşleştir
        cmd.show("sticks", "lig")
        cmd.color("blue", "lig")
        if use_chain:
            cmd.show("surface", f"polymer and chain {chain}")
            cmd.color("green", f"polymer and chain {chain}")
            cmd.set("transparency", 0.7, f"polymer and chain {chain}")
        else:
            cmd.show("surface", "polymer")
            cmd.color("green", "polymer")
            cmd.set("transparency", 0.7, "polymer")
        if cmd.get_names('selections') and 'interacting_res' in cmd.get_names('selections'):
            cmd.show("sticks", 'interacting_res')
            cmd.color("red", 'interacting_res')

    print(f"center_x = {x:.3f}")
    print(f"center_y = {y:.3f}")
    print(f"center_z = {z:.3f}")
    print(f"size_x = {x_size:.1f}")
    print(f"size_y = {y_size:.1f}")
    print(f"size_z = {z_size:.1f}")

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        gridbox_path = os.path.join(output_dir, f"{pdb_id}_gridbox.txt")
        pse_path = os.path.join(output_dir, f"{pdb_id}_gridbox.pse")
    else:
        gridbox_path = f"{pdb_id}_gridbox.txt"
        pse_path = f"{pdb_id}_gridbox.pse"

    with open(gridbox_path, "w") as f:
        f.write(f"center_x = {x:.3f}\n")
        f.write(f"center_y = {y:.3f}\n")
        f.write(f"center_z = {z:.3f}\n")
        f.write(f"size_x = {x_size:.1f}\n")
        f.write(f"size_y = {y_size:.1f}\n")
        f.write(f"size_z = {z_size:.1f}\n")
    print(f"Grid box parametreleri '{gridbox_path}' dosyasına kaydedildi.")
    # Also print the gridbox file contents to stdout for easy piping/inspection
    try:
        with open(gridbox_path, 'r') as gf:
            print('--- gridbox content ---')
            print(gf.read())
    except Exception as e:
        print(f"Gridbox dosyası okunamadı: {e}")

    # Also write lowercase filename for compatibility with pipelines expecting lowercase
    gridbox_path_lower = gridbox_path.replace(pdb_id, pdb_id.lower())
    if gridbox_path_lower != gridbox_path:
        with open(gridbox_path_lower, "w") as f:
            f.write(f"center_x = {x:.3f}\n")
            f.write(f"center_y = {y:.3f}\n")
            f.write(f"center_z = {z:.3f}\n")
            f.write(f"size_x = {x_size:.1f}\n")
            f.write(f"size_y = {y_size:.1f}\n")
            f.write(f"size_z = {z_size:.1f}\n")
        print(f"Grid box (lowercase) parametreleri '{gridbox_path_lower}' dosyasına da kaydedildi.")

    if output_dir:
        res_path = os.path.join(output_dir, f"{pdb_id}_interacting_res.txt")
        with open(res_path, "w") as handle:
            for chain_id, resi, resn in sorted(interacting_residues):
                handle.write(f"{chain_id} {resi} {resn}\n")
        res_path_lower = res_path.replace(pdb_id, pdb_id.lower())
        if res_path_lower != res_path:
            with open(res_path_lower, "w") as handle:
                for chain_id, resi, resn in sorted(interacting_residues):
                    handle.write(f"{chain_id} {resi} {resn}\n")

    # If an output directory is provided, always save a PyMOL session (PSE)
    # so downstream pipelines can inspect the visualization without extra flags.
    if output_dir is not None:
        try:
            visualize_gridbox((x, y, z), (x_size, y_size, z_size))
        except Exception:
            # visualization may fail silently in some headless setups; continue
            pass
        try:
            # Ensure protein and ligand are visible in the saved session
            try:
                if cmd.count_atoms('lig') > 0:
                    cmd.show('sticks', 'lig')
                    cmd.color('cyan', 'lig')
            except Exception:
                pass
            try:
                if use_chain:
                    cmd.show('surface', f'polymer and chain {chain}')
                    cmd.color('green', f'polymer and chain {chain}')
                    cmd.set('transparency', 0.5, f'polymer and chain {chain}')
                else:
                    cmd.show('surface', 'polymer')
                    cmd.color('green', 'polymer')
                    cmd.set('transparency', 0.5, 'polymer')
            except Exception:
                pass
            cmd.save(pse_path)
            print(f"PyMOL oturumu '{pse_path}' dosyasına kaydedildi.")
        except Exception as e:
            print(f"PSE kaydedilemedi: {e}")
    # Also attempt to save a PSE in the current directory (fallback) so caller
    # always has a visualization file even if no output_dir was provided.
    try:
        visualize_gridbox((x, y, z), (x_size, y_size, z_size))
    except Exception:
        pass
    try:
        # Ensure protein and ligand visible for fallback save as well
        try:
            if cmd.count_atoms('lig') > 0:
                cmd.show('sticks', 'lig')
                cmd.color('cyan', 'lig')
        except Exception:
            pass
        try:
            if use_chain:
                cmd.show('surface', f'polymer and chain {chain}')
                cmd.color('green', f'polymer and chain {chain}')
                cmd.set('transparency', 0.5, f'polymer and chain {chain}')
            else:
                cmd.show('surface', 'polymer')
                cmd.color('green', 'polymer')
                cmd.set('transparency', 0.5, 'polymer')
        except Exception:
            pass
        cmd.save(pse_path)
        print(f"PyMOL oturumu (fallback) '{pse_path}' dosyasına kaydedildi.")
    except Exception as e:
        print(f"Fallback PSE kaydedilemedi: {e}")


def visualize_gridbox(center, size):
    """
    PyMOL içinde grid box'ı görselleştiren fonksiyon.
    """
    x, y, z = center
    x_size, y_size, z_size = size

    x_min, x_max = x - x_size/2, x + x_size/2
    y_min, y_max = y - y_size/2, y + y_size/2
    z_min, z_max = z - z_size/2, z + z_size/2

    corners = [
        (x_min, y_min, z_min), (x_max, y_min, z_min),
        (x_max, y_max, z_min), (x_min, y_max, z_min),
        (x_min, y_min, z_max), (x_max, y_min, z_max),
        (x_max, y_max, z_max), (x_min, y_max, z_max)
    ]
    for i, (cx, cy, cz) in enumerate(corners):
        cmd.pseudoatom(f"gridbox_corner_{i+1}", pos=(cx, cy, cz))

    edges = [
        (1, 2), (2, 3), (3, 4), (4, 1),
        (5, 6), (6, 7), (7, 8), (8, 5),
        (1, 5), (2, 6), (3, 7), (4, 8)
    ]
    for e1, e2 in edges:
        cmd.distance(f"grid_edge_{e1}_{e2}", f"gridbox_corner_{e1}", f"gridbox_corner_{e2}")

    cmd.color("yellow", "gridbox_corner_*")
    cmd.color("red", "grid_edge_*")

    print("Grid box görselleştirildi!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoGrid PDB ID'den grid box parametreleri oluşturma aracı")
    parser.add_argument("-p", "--pdb_id", type=str, required=True, help="İşlenecek PDB ID")
    parser.add_argument("-r", "--receptor", type=str, help="Reseptör dosyası yolu (opsiyonel)")
    parser.add_argument("-l", "--ligand", type=str, help="Ligand ismi (opsiyonel) - belirtilmezse ilk ligand kullanılır")
    parser.add_argument("-c", "--chain", type=str, default="A", help="Çıkarılacak zincir (varsayılan: A)")
    parser.add_argument("-o", "--output_dir", type=str, help="Çıktı dosyalarının kaydedileceği dizin")
    parser.add_argument("--visualize", action="store_true", help="Grid box görselleştirmesi oluştur")
    
    args = parser.parse_args()
    
    pdb_id_to_process = args.pdb_id
    print(f"İşleniyor PDB ID: {pdb_id_to_process}")
    print(f"Seçilen zincir: {args.chain}")
    
    try:
        # PyMOL başlat (quiet, no GUI)
        pymol.pymol_argv = ['pymol', '-cq']
        pymol.finish_launching()

        # Grid box oluştur (respect visualize flag)
        setup_pymol_gridbox(
            pdb_id_to_process,
            args.output_dir,
            args.ligand,
            args.chain,
            receptor_path=args.receptor,
            visualize=args.visualize,
        )

        print("İşlem tamamlandı.")

        # PyMOL'ü temizle ve kapat
        try:
            cmd.delete("all")
            cmd.reinitialize()
        except Exception:
            pass
        try:
            cmd.quit()
        except Exception:
            pass
        # Forcefully exit to ensure no lingering PyMOL threads prevent shell return
        try:
            os._exit(0)
        except Exception:
            sys.exit(0)
    except Exception as e:
        print(f"İşlem sırasında hata oluştu: {e}")
        try:
            cmd.quit()
        except Exception:
            pass
        try:
            os._exit(1)
        except Exception:
            sys.exit(1)
    # safety net
    try:
        os._exit(0)
    except Exception:
        sys.exit(0)
REQUEST_TIMEOUT = 5
