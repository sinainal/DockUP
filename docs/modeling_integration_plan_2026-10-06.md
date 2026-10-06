# DockUP — Nanoplastic Studio, xTB/DFT ve homoloji entegrasyon planı

Tarih: 2026-10-06. Bu dosya ilk tasarımın tarihsel kaydıdır. Sonraki kullanıcı
kararıyla Prepare/Quantum sekmeleri yerine bağımsız xTB/DFT ekranı ve Docking
içinde homoloji popup'ı uygulanmıştır. Güncel kapsam, testler ve eksikler için
`modeling_workbench.md` ve `nanoplastic_studio_feature_inventory.md` esas alınır;
aşağıdaki plan maddeleri tamamlanmış özellik anlamına gelmez.

## 1. Karar ve kapsam

DockUP kullanıcıya tek araştırma arayüzü sunmalı. Nanoplastic Studio'nun hesap
mantığını yeniden yazmak yerine sürümlenmiş bir adaptörle kullanmalı; homoloji ve
kuantum hesapları kendi Python ortamlarında ayrı worker süreçleriyle çalışmalı.
İlk sürüm yerel kullanım içindir. Kubernetes/Celery/Redis zorunlu değildir.

Önerilen ürün bölümleri: Prepare, Docking, Results, Report. Prepare içinde
Ligand/Polymer, Receptor/Homology ve Quantum sekmeleri bulunur. Hesap kuyruğu ve
3B görüntüleyici bütün bölümlerde ortaktır. Mevcut docking/redocking davranışı
ilk aşamada değişmeden, uyumluluk adaptörü üzerinden korunur.

Varsayım: xTB/DFT ilk olarak küçük molekül/oligomer hazırlama ve mevcut
polimer–küçük molekül adsorpsiyon çalışmalarına eklenecek. Tüm reseptör pentamerine
DFT uygulanması veya docking skorunun kuantum bağlanma serbest enerjisine
çevrilmesi bu planın kapsamı değildir. Reseptör–ligand QM küme/QM/MM çalışmaları
ayrı, sonraki bir özellik olarak tasarlanmalıdır.

## 2. Yerel incelemede doğrulanan durum

| Bileşen | Mevcut durum | Entegrasyon sonucu |
| --- | --- | --- |
| DockUP | FastAPI; Jinja/vanilla JS; NGL; SDF/PDB yükleme; ligand-3d alt uygulaması; docking kuyruğu; hash tabanlı prepared_artifacts | Arayüz ve docking motoru korunabilir; React dönüşümü gerekmez |
| Nanoplastic Studio | `/home/sina/Downloads/nanoplastic-modeling-studio`; ayrı özel araştırma arşivi mevcut | Genel uygulama kaynakları ile özel veri/arşiv ayrımı korunur |
| Studio modelleri | RDKit üretim; PE/PET/PP/PS; API'de tekrar sayısı 1–24, konformer isteği 1–32; SDF/PDB/XYZ çıktıları | Tek oligomer oluşturma doğrudan liganda bağlanabilir; bu sınırlar bilimsel geçerlilik eşiği değildir |
| xTB | GFN2 runner, su/çözücüsüz, optimizasyon/SP, yük/UHF, sabit yüzey veya tam optimizasyon, zaman aşımı ve yakınsama kontrolleri | Yeniden yazmak yerine parametreli, sürümlenmiş runner olarak kullanılır |
| CREST | Opsiyonel ayrı motor; kısıtlı hesap için sürüm seçimi kodda mevcut | Konformer/poz örnekleme dalı; motora özgü smoke test gerekir |
| Studio iş takibi | API'deki job sözlükleri bellekte; ThreadPoolExecutor; sonuç dosyaları disk üzerinde | Uygulama yeniden başlatılınca iş durumunu kurtarmak için kalıcı job kaydı gerekli |
| Optimize çıktı | xTB XYZ ve görüntüleme için dönüştürülmüş PDB sunuluyor; structure.sdf başlangıç modeli | Optimize edilmiş koordinatları orijinal bağ grafiğine uygulayan yeni SDF export gerekli |
| DFT | İncelenen uygulama API/runner kaynaklarında DFT çalıştırıcı bulunmadı; özel arşivde 2026-09-11 tarihli plan var | Gerçek DFT adaptörü yeni geliştirilecek; eski plan çalışmış sonuç sayılmayacak |
| Homoloji | Serotonin altında ProMod3/şablon hizalama/hibrit model/değerlendirme scriptleri mevcut | Çalışma-özel sabitler kaldırılarak ayrı DockUP modülü yapılacak |
| ORCA adı | `/usr/bin/orca`, dpkg kaydına göre GNOME ekran okuyucusu | Kimya motoru olarak kabul edilemez; sadece `which orca` yeterli değil |

PATH üzerinde xTB/CREST bulunmaması bütün makinede kurulmadıkları anlamına gelmez;
arşivde paketlenmiş motorlar bulunuyor. Çalışabilirlik, sürüm ve plugin uyumu ayrıca
kanıtlanmalı. İnceleme sırasında DockUP worktree'sinde mevcut değişiklikler vardı;
bu plan onları değiştirmedi.

İncelenen kaynaklar:

- DockUP: `docking_app/app.py`, `state.py`, `services.py`, `prepared_artifacts.py`,
  `control/models.py`, `ligand_3d/app.py`, `templates/index.html`.
- Studio: `api.py`, `builder.py`, `xtb.py`, `crest.py`, `pyproject.toml`, `README.md`;
  özel arşivdeki `modeling/app` ve `research steps/dfT_validation_plan_2026-09-11.md`.
- Homoloji: `serotonin/code/model_ab_joint_context_v2.py`, `assess_ab_evidence.py`,
  `run_ab_reference_controls.py`. Bunlar genel homoloji API'si değildir.

## 3. Mimari

```text
DockUP UI / API / Control / MCP
             |
       Project + Artifact registry
             |
      Persistent Job manager (SQLite, local)
             |
       +-----+------------------+------------------+
       |                        |                  |
  Modeling adapter         Homology worker     Docking adapter
  Studio local service     ProMod3 / optional   Existing runner
  xTB / CREST / DFT        MODELLER             Vina + PLIP
       |                        |                  |
       +----- versioned outputs + provenance ------+
```

İlk entegrasyon Studio'nun localhost API'siyle yapılır; DockUP native panelleri
bu adaptörü kullanır. iframe ancak geçici inceleme köprüsüdür, final UI değildir.
Studio sunucusu ayrı ortamda çalışır: RDKit/Numpy/ProMod3/OpenMM bağımlılıkları
DockUP ortamına kontrolsüzce taşınmaz. Daha sonra bilimsel runner'lar bağımsız
pakete çıkarılabilir; bu ilk işin ön koşulu değildir.

Tek kaynak koordinat hash'leriyle snapshot'lanan artifact registry'dir. Adapter
motor çıktısını içe alır; mutasyona açık `latest` dosyasını kalıcı referans yapmaz.
Studio'nun eski işleri read-only içe alınır, otomatik yeniden çalıştırılmaz.

### İş yönetimi

- SQLite job tablosu; durumlar: queued, running, succeeded, partially_succeeded,
  failed, cancelled, timed_out, interrupted, blocked.
- İş tipleri: ligand_build, conformer_sample, xtb_opt, xtb_sp, dft_sp, dft_opt,
  dft_freq, homology_build, model_assess, ligand_transfer, docking, plip, report.
- `transport_finished` ve `scientific_succeeded` ayrılır. Studio'nun bir batch'i
  completed döndürmesi, içindeki bütün hesapların yakınsadığı anlamına gelmez.
- Her işin input artifact hash'leri, protokolü, kod/motor sürümü, argv, stdout/
  stderr, kaynak sınırları, çıkış kodu, yakınsaması ve çıktıları kaydedilir.
- Ayrı process group; terminate/kill ve child süreç temizliği; UI'da Cancel.
  API restart'ında kayıp işler interrupted olur; resume destekleniyorsa motorun
  checkpoint'i doğrulanır, aksi halde yeni child job oluşturulur.
- CPU thread/RAM rezervasyonu ortak bütçeden yapılır. Vina, xTB ve DFT aynı anda
  bütün CPU'ları talep edemez. İlk varsayılan bir ağır kuantum işi; sayısal kaynak
  sınırları makinede küçük benchmark sonrası belirlenir.
- Aynı protokol/hash için cache kullanılabilir, ama seed/çözücü/yük/sürüm gibi
  sonucu etkileyen alanlar cache anahtarından çıkarılamaz. Export idempotent olur.

## 4. Veri sözleşmesi ve en önemli doğruluk bariyeri

Her artifact: `artifact_id`, `project_id`, `kind`, `parent_ids`, `path`, `sha256`,
`schema_version`, `source_job_id`, `protocol_id`, `created_at`, `warnings`.

Molekül verisi: bağ dereceleri, aromatiklik, formal charge, stereokimya, stabil
atom ID'leri, koordinat birimi, mikro-durum, tekrar sayısı, uç gruplar/taktisite,
konformer ID'si. XYZ atom sayısı ve element dizisi orijinal SDF ile doğrulanır;
koordinatlar bağ grafiğini yeniden tahmin etmeden SDF'ye uygulanır. Atom sırası
değiştiyse explicit birebir atom map zorunludur. Sonlu koordinatlar, bağ uzunluğu
anomalileri ve stereokimyasal değişimler kontrol edilir; açıklanmayan kimyasal
değişim varsa otomatik Dock-ready yayını durur.

Reseptör verisi: UniProt/dizi sürümü, template kimliği/hash'i, alignment, gerçek
chain ID → alt birim tipi, hedef/şablon/auth numaralandırma ve insertion code map'i,
assembly, state/ligand bağlamı, modellenen bölgeler, constraint lineage,
yerel/global kalite ve cep tanımı. Chain adı alt birim tipi değildir.

Üç farklı kimlik birbirine karıştırılmaz: deneysel yapı, karşılaştırmalı model,
AlphaFold-tabanlı hibrit. AFDB monomer içe alımı AF3 kompleks tahmini sayılmaz.

### Dock-ready export

1. Kullanıcı başlangıç / xTB-optimize / DFT-optimize geometriden birini seçer.
2. Atom/topoloji ve protokol doğrulaması tamamlanır; artifact dondurulur.
3. DockUP ligand/receptor kataloğuna yeni sürüm olarak eklenir, orijinal korunur.
4. Meeko/PDB2PQR hazırlığının ağır atomları değiştirip değiştirmediği denetlenir.
5. Docking manifesti hem model kaynağını hem hazırlanan PDBQT hash'ini taşır.

xTB/DFT partial charges mevcut Vina akışına sessizce aktarılmaz. Motorun kullandığı
hazırlama/charge protokolü ayrıca kaydedilir; kuantum geometri kullanmak, aynı
kuantum charge modelini docking'de kullanmak demek değildir.

## 5. Ligand/Polymer UI ve akış

Prepare → Ligand/Polymer:

- Girdi: PE/PET/PP/PS veya SMILES/SDF; tekrar sayısı; uç grup ve stereokimya
  seçenekleri yalnızca gerçekten implement edilmişse aktif olur.
- Yapı tipi: Single oligomer, Surface proxy, Imported particle. Tek oligomer
  Dock-ready'ye taşınabilir; disconnected yüzey parçası otomatik Vina ligandı
  olarak yayınlanmaz. Her yapının atom sayısı ve temsil sınırı gösterilir.
- Konformer sayısı, seed, mikro-durum/yük. pH etiketi otomatik sabit-pH simülasyon
  anlamına gelmez; özel moleküllerde protonasyon değişti varsayılmaz.
- Build → force-field yakınsama → isteğe bağlı CREST/xTB örnekleme → geometri
  seçimi → Add to docking. Aynı molekülün farklı konformerlerini ayrı sürümler
  olarak seçmek mümkün olur; xTB enerjileri farklı bileşimler arasında doğrudan
  konformer enerji aralığı gibi karşılaştırılmaz.
- Sağ NGL: başlangıç/optimize üst üste görüntü, konformer seçimi, atom eşlemesi.
  Alt tabloda yakınsama, süre, enerji/protokol ve uyarılar.

Mevcut Advanced Ligand Fetch kaldırılmaz. Yeni Modeling açılışı bu panelle aynı
Dock-ready katalog ve export yolunu kullanır; ikinci ayrı ligand deposu yaratılmaz.

## 6. Quantum UI: xTB ve DFT

Prepare → Quantum iki amacı ayrı sunar:

- **Structure refinement:** izole molekül/oligomer geometrisi, konformer sıralama.
- **Interaction study:** polimer + adsorbate için mevcut Studio pose_sets akışı;
  receptor docking'inden ayrı deney tipi. Kompleks ve fragment map'leri açık.

Alanlar: motor, hesap türü (SP/Opt/Frequency), yöntem, çözücü modeli, toplam yük,
spin, sabit atomlar, CPU/RAM, timeout ve sonuçların yayınlanacağı geometri.
Sadece desteklenen alanlar görünür; ayrıntılar Advanced altında. Charge/elektron
sayısı ve seçilen spin tutarlılığı denetlenir; xTB UHF ile DFT multiplicity alanları
provider-specific olarak çevrilir ve kaydedilir, kör bir eşitlik varsayılmaz.

### xTB

İlk önerilen protokol GFN2-xTB/ALPB-water; gaz fazı ayrı protokol. Optimizasyon
başarısı yalnız dosya varlığıyla değil, sonlu enerji, olumlu optimizer çıktısı,
SCC durumu ve beklenen geometri dosyasıyla doğrulanır. Mevcut runner kontrolleri
korunur ve restart/cancel/timeout regresyonları eklenir. [xTB optimization docs](https://xtb-docs.readthedocs.io/en/latest/optimization.html).

### DFT motoru

İlk iki adaptör: ORCA (kullanıcının ayrı kurduğu lisanslı motor), PySCF (ayrı
Python ortamı). ORCA hazır değilse UI Unavailable gösterir; PySCF'ye sessiz yöntem
değişikliği yapılmaz. [ORCA kullanım modeli](https://www.faccts.de/orca/).

Pilot tercih: ORCA r2SCAN-3c; su hesabında seçilen CPCM protokolü ayrıca sabitlenir.
Bu composite yöntem D4 ve gCP içerir; ayrıca başka dispersion/counterpoise eklemek
varsayılan değildir. [ORCA 3c yöntemleri](https://orca-manual.mpi-muelheim.mpg.de/contents/modelchemistries/3cmethods.html).

Alternatif: PySCF PBE0-D3(BJ)/def2-TZVP, desteklenen PCM/diğer çözücü modeliyle;
dispersion extension ve geometri optimizer kabiliyeti worker ortamında doğrulanır.
Bu r2SCAN-3c'nin aynı implementasyonu değildir. [PySCF DFT](https://pyscf.org/user/dft.html),
[solvent](https://pyscf.org/user/solvent.html).

İlk DFT milestone'u SP ve parser; daha sonra Opt ve Frequency. SCF yakınsaması,
geometri yakınsaması ve Hessian/minimum kontrolü ayrı durumlar olur. Frekans işi
yapılmadıysa UI "minimum doğrulandı" demez. Timeout benchmark sonrası; bütün
reseptöre DFT çağrısı MVP'de engellenir, çok büyük ligand açık kaynak bütçesi ister.

### Enerji karşılaştırması

Kompleks geometrisinden çıkarılmış iki fragment aynı yöntem/basis/çözücü/yük
protokolüyle hesaplanır:

`ΔE_int = E_complex − E_fragment1(frozen) − E_fragment2(frozen)`.

Deformasyon/binding karşılaştırması için aynı seviyedeki izole referanslar da
gerekir. xTB fragment enerjileri DFT kompleks enerjisinden çıkarılamaz. ALPB–CPCM
farkı yöntem farkından ayrı bir karıştırıcıdır; eş-geometri gaz fazı tanısal dal
eklenebilir. Continuum fragment cavity konvansiyonu açıkça kaydedilir; BSSE/basis
duyarlılığı protokole göre yönetilir. Elektronik/continuum enerji döngüsü ΔG/Kd
olarak sunulmaz; Vina skoru ile tek birleşik puana indirgenmez.

DFT altkümesi sonuçlar görülmeden sabitlenir: yalnız en iyi xTB pozları değil,
güçlü/orta enerji ve farklı temas motifleri. İlk küçük pilotun sayısı kaynak
benchmark'ına göre seçilir, örnek olarak dört polimerden ikişer kompleks kullanılabilir.

## 7. Receptor/Homology UI ve akış

Wizard adımları:

1. **Target:** FASTA/UniProt; tür; zincirler; monomer/oligomer; stoikiometri ve
   assembly. Human A ile mouse A veya chain B ile B subtype karışmaz.
2. **Template:** kullanıcı PDB/mmCIF veya aday arama; identity, coverage, eksik
   bölgeler, deneysel yöntem/resolution, bağlı ligand/state, assembly ve hedef
   ceple örtüşme. Yalnız en iyi resolution'a göre otomatik seçim yapılmaz.
3. **Alignment & assembly:** dizi görünümü, gap/loop bölgeleri, residue mapping;
   NGL'de chain renkleri; her zincir için hedef dizi ve template; interfacial cep
   varsa iki taraf birlikte. Kullanıcı kritik bölgeyi onaylar.
4. **Build:** önce ProMod3 provider; opsiyonel lisanslı MODELLER. Model sayısı,
   desteklenen loop/side-chain/refinement ve constraints açık. Bağımsız random
   model üretimi desteklenmiyorsa "10 independent models" düğmesi yoktur;
   farklı şablon/parametre adaylarının ortak kökeni kaydedilir.
5. **Assess:** dizi/coverage, zincir bağları, Ramachandran/rotamer/Cβ, clashes,
   global ve cep-özel ölçümler; QMEANDisCo ve membran hedeflerinde uygun QMEANBrane
   opsiyonel provider. Eksik/ulaşılamayan ölçüm Missing olur, Passed değil.
6. **Pocket & controls:** native ligand, transferred ligand, predicted pocket,
   manual seçimleri ayrı etiketli. Transfer için diziye duyarlı bölge hizalaması,
   CA count/RMSD, transformation, referans ligand ve atom centroid'i saklanır.
   Transfer kontrolü cross-docking, gerçek native bağlı yapı kontrolü redocking
   adını alır. Grid sadece modelle aynı koordinat çerçevesindeyse devralınır.
7. **Select & export:** kalite/cep tablosu, belirsizlik ve kaynaklar görüldükten
   sonra kullanıcı modeli seçer; freeze → Add to receptors. Düşük skorlu adaylar
   silinmez. Model seçimi test polimer docking skorları görülmeden dondurulur.

ProMod3 template+alignment temelli pipeline sunuyor; bu mevcut yerel deneyimin
genelleştirilmesi için uygun ilk provider. [ProMod3 pipeline](https://openstructure.org/promod3/3.7.0/modelling/pipeline/).
MODELLER opsiyoneldir, akademik kullanım/lisans kaydı şartları vardır; binary ve
license key DockUP içine dağıtılmaz. [MODELLER lisans/kurulum](https://salilab.org/modeller/download_installation.html).

Kalite tek bir global geçer/kaldı skoruyla tanımlanmaz. Dizinin/atomların yanlış
olması, kopuk peptide bağları ve açıklanmayan ciddi cep clashes gibi temel sorunlar
export'u durdurur. Diğer metrikler model sınıfı/domain/cep için önceden belirlenmiş
profilde yorumlanır. Geometrik düzgünlük deneysel atomik doğruluk kanıtı değildir.
Serotonin çalışmasında kullanılmayan tarihsel validator zorunlu gate veya rapor
metnine geri eklenmez; başka bir metriğin yokluğu da başarılı ölçüm gibi gizlenmez.

AFDB import ayrı sağlayıcı/etiket; gelecekte kompleks tahmini varsa homoloji
adıyla sunulmaz. Harici server'a dizi/yapı yükleyen provider yerel provider'dan
ayırt edilir; kullanıcı dış aktarımı açıkça seçer. Hizmet koşulları kontrol edilir.

## 8. UI davranışı

Solda parametre/wizard, ortada ortak NGL, altta/solda sonuç tablosu; sağda isteğe
bağlı detay drawer. Mevcut DockUP Inter/Montserrat ve mavi-gri stil korunur.

- Sabit queue drawer: stage, elapsed, ayrılan CPU/RAM, gerçek log, Cancel, Resume.
- Yüzde ancak aşama sayısı biliniyorsa gösterilir; belirsiz hesap süresine sahte
  yüzde veya kesin ETA verilmez.
- Artefact seçimi: Original / xTB / DFT / Selected model; kaynak ve hash ulaşılabilir.
- Results filtreleri: Docking, Quantum, Homology; ölçüler ayrı sütun ve grafiklerde.
- Primary/sensitivity seçimi ve provenance bir bakışta görünür; "validated model"
  rozeti yerine "computationally assessed" ve yapılan kontrollerin listesi.
- Eksik motor paneli anlaşılır kurulum/adres notuyla devre dışı; yalnız executable
  adı değil sürüm/kimlik/smoke test sonucu gösterilir.
- Responsive görünümde viewer ayrı tab olabilir; tablolar yatay scroll; loglar
  varsayılan collapsed. Screenshot testleri çakışma/simetriyi denetler.

## 9. Önerilen uygulama yüzeyi — yeni API'ler

Mevcut `/api/ligands`, `/api/receptors`, Control ve docking endpoint'leri korunur.

```text
GET  /api/modeling/capabilities
POST /api/modeling/ligands
POST /api/modeling/homology/projects
POST /api/modeling/homology/projects/{id}/alignment
POST /api/modeling/homology/projects/{id}/build
POST /api/modeling/quantum/jobs
GET  /api/jobs/{id}
GET  /api/jobs/{id}/events
POST /api/jobs/{id}/cancel
POST /api/jobs/{id}/resume
GET  /api/artifacts/{id}
POST /api/artifacts/{id}/publish
```

Publish hedefi `dock_ligand`, `dock_receptor` veya `analysis_only`; kullanıcı
hangi geometriyi yayınladığını seçer. API raw host path kabul etmek yerine artifact
ID kullanır; canonical root/allowlist, dosya boyutu, safe argv, erişim ve atom
eşlemesi kontrol edilir. Localhost API bile keyfi shell/host dosya okuma sunmaz.
Agent/MCP aynı kontrol servislerini kullanır; UI'ya görünmeyen ayrı işlem yolu yok.

Önerilen yeni kod: `docking_app/modeling/{adapters,artifacts,jobs,schemas}`,
`routes/modeling.py`, `static/modeling/{ligands,quantum,homology}.js` ve bağımsız
worker entrypoint'leri. Bugünkü binlerce satırlık app.js'e yeni monolit eklenmez.

## 10. Milestone ve çıkış koşulları

| Aşama | Çıktı | Kabul kontrolü |
| --- | --- | --- |
| M0: sözleşme | artifact/job schema, capability probe, Studio adaptörü, mevcut docking baseline | Yanlış ORCA kimliği reddedilir; backend unavailable UI'yi çökertmez; eski testler geçer |
| M1: ligand+xTB dikey akış | native panel, build, opt, optimize SDF, Dock-ready export | Taze PE/PET/PP/PS örneklerinde atom/bond/stereo korunur; eski SDF export hatası yakalanır; bir küçük gerçek Vina/PLIP zinciri çalışır |
| M2: homoloji dikey akış | FASTA/template/alignment/build/QA/pocket/export UI | Genel monomer örneği ve ayrı AABAB regresyonu; kötü numbering/ring/gap/pocket negatif testleri; native/cross ayrımı |
| M3: DFT SP | provider, lisans/kimlik kontrolü, küçük benchmark, frozen-fragment cycle UI | Bütün cycle işleri aynı protokol; SCF failure/birim/charge hatası yakalanır; xTB–DFT enerji karışımı reddedilir |
| M4: ileri akış | DFT Opt/Freq, CREST/adsorption, pipeline dependency graph, resume | Cancel child process bırakmaz; restart/retry orijinali ezmez; checkpoint/hash kontrolü; seçilmiş küçük cycle pilotu |
| M5: yayın çıktısı | model/pocket/quantum Methods provenance, aynı rapor formatı, E2E | Mevcut figure/citation/template kontrolleri korunur; başarısız/missing ölçüm başarı diye yazılmaz; UI responsive görsel testleri |

M2 ile M3 sırası prototip geri bildirimine göre değişebilir; ortak registry/job
altyapısı ikisinin ön koşuludur. Takvim ve DFT runtime yerel benchmark yapılmadan
kesinleştirilmez. Önce bir uçtan uca çalışan küçük akış, sonra toplu kampanyalar.

Ek regresyonlar: atom sırası/element uyuşmazlığı; XYZ yanlış birim; duplicate
publish; disconnected surface; konformer yerine yanlış input; spin/yük/parite;
SCF/opt normal-termination farkı; assembly isimlendirme; yanlış ligand transfer
frame'i; PDB insertion code; şablon kısıtını bağımsız doğrulama sayma; API restart;
ölü worker; engine version değişiminde cache invalidation; izinsiz path traversal.

## 11. Başarı tanımı

Kullanıcı tek DockUP arayüzünden oligomeri üretir, xTB veya seçilmiş DFT ile
geometrisini değerlendirir, orijinali koruyarak docking'e gönderir; hedef
reseptörü şablon/diziyle modeller, cep-özel kaliteyi görür ve kontrol türünü doğru
etiketler. Her figür/sonuç yeniden bulunabilir kaynak dosya ve protokole bağlıdır.
Bu ürün hedefi; bağlanma, biyolojik etki veya hakem kabulü garantisi değildir.
