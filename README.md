# Ay İniş Alanı Navigatörü

2gbram takımının NASA Space Apps Challenge projesi. İlk MVP, Ay'daki konum ve tarih seçeneklerini Güneş/Dünya görünürlüğüne göre karşılaştırır.

## Mevcut durum

Task 0 tamamlandı: servis kurulumu, bağımlılık kilitleri, temiz kurulum ve başlangıç arazi/nokta kaynakları doğrulandı. Backend sağlık endpoint'i, ortak veri modelleri ve başlangıç arayüzü mevcut. Task 1 bu dalda uygulandı: RabbitMQ istekleri Güneş/Dünya verisi ve kalıcı LOLA arazi kesiti üretir. Task 2 arazi ufkunu, Task 3 görünürlük aralıklarını ve süre metriklerini hesaplar. Task 4 de bu dalda tamamlandı: kalıcı analiz API’si, NASA modelleriyle kaydırmalı 3D giriş, konum/tarih seçimi ve gerçek sonuçların zaman kontrollü arayüzü mevcut.

## Kurulum

Docker Desktop ve Docker Compose gerekir. Proje kökünde `.env.example` dosyasını `.env` olarak kopyalayın ve yerel RabbitMQ kullanıcı adı/parolasını belirleyin. URL içinde kullanılabilecek karakterler seçin.

```bash
cp .env.example .env
docker compose up --build
```

- Arayüz — http://localhost:5173
- API belgeleri — http://localhost:8000/docs
- Sağlık kontrolü — http://localhost:8000/health
- RabbitMQ yönetimi — http://localhost:15672

Worker kalıcı RabbitMQ kuyruğunu tüketir; dosyaları doğrulayıp sonuç mesajını yayımladıktan sonra isteği onaylar. Backend kaynak değişikliklerini yeniden yükler. Worker değişikliklerinde `docker compose restart data-ingestion` kullanın.

## Kontroller

```bash
docker compose --env-file .env.example config --quiet
docker compose run --rm backend pytest
docker compose run --rm data-ingestion pytest
docker compose run --rm frontend npm test
docker compose run --rm frontend npm run build
docker compose run --rm backend ruff check app tests /workspace/shared
docker compose run --rm data-ingestion ruff check app tests
```

Doğrudan bağımlılıklar sabitlendi. Frontend Vite 7.3.8 ve Vitest 4.1.12 kullanır; kilit dosyası güncellendi ve `npm ci` ile doğrulandı. 14 Kasım 2026 kontrolünde `npm audit` sıfır açık bildirdi; Task 4 sonunda 192 test (112 backend, 75 veri alımı, 5 arayüz), Python lint kontrolleri ve frontend derlemesi geçti. Python geçişli bağımlılıkları `requirements.lock` dosyalarında sürüm ve SHA-256 hash ile kilitlendi; Docker kurulumu `--require-hashes` ve `pip check` kullanır. [Kilitleri güncelleme](docs/python-dependencies.md).

```bash
docker compose run --rm --no-deps frontend npm ci
```

14 Kasım 2026 tarihinde ayrı ve temiz bir Git klonunda önbelleksiz Docker derlemesi, yeni veri diskleriyle başlangıç ve 70 test doğrulandı. [Temiz kurulum raporu](docs/clean-install-verification.md).

## Horizons verilerini indirme

```bash
docker compose --env-file .env.example run --rm --no-deps data-ingestion \
  python -m app.download_horizons examples/horizons-reference.json
```

Referans noktasında 14–15 Kasım 2026 için saatlik 25 örnek indirildi; tekrar çalıştırma önbelleği kullandı. Ham yanıtlar, kaynak bilgileri ve eşleştirilmiş Güneş/Dünya verileri ortak `/data/horizons/` dizininde saklanır. Tek başına Horizons komutu kullanılabilir; aşağıdaki uçtan uca kuyruk komutu araziyi de hazırlar. [Ayrıntılar ve doğrulama](docs/horizons-ingestion.md).

Başlangıç karşılaştırma noktaları Mons Mouton merkezi ve Malapert Massif R23 olarak kaynaklandırıldı. İki noktanın çevresindeki DEM pencerelerinde veri kapsamı doğrulandı. Bunlar güvenli iniş noktası sertifikası değildir; uygulama içi nokta seçimi, arazi ufku hesabı ve görünürlük analizi sonraki task’larda eklendi. [Arazi ve koordinat kaynakları](docs/terrain-and-sites.md).

## Task 1 — Kuyruk üzerinden veri hazırlama

Başka bir dalın çalışan ortamına dokunmadan ayrı Docker projesi açmak için:

```bash
docker compose -p nasa-task1 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.isolated.yml up -d --build --wait backend data-ingestion

docker compose -p nasa-task1 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.isolated.yml exec -T backend \
  python -m app.submit_ingestion /workspace/shared/examples/ingestion-malapert.json
```

İlk istek yaklaşık 189 MB'lık NASA arazi kaynağını indirir. Aynı istek kimliği ve parametreler tekrar kullanıldığında doğrulanmış sonuç döner; yeni senaryo için yeni UUID gerekir. Veriler projenin kalıcı `lunar-data` diskinde tutulur. `down --volumes` bu verileri siler; korumak için yalnızca `down` kullanın. İzole yapılandırma host portu açmaz.

Malapert için 25 zaman örneği ve 501 × 501 arazi kesiti gerçek kuyruk akışıyla doğrulandı. RabbitMQ yeniden başlatıldığında bekleyen istek korundu. [Akış, hata davranışları ve kanıtlar](docs/ingestion-pipeline.md).

Bu çalışma yalnızca `feature/task1-ingestion-cache` dalına gönderilir; `tahakrcc` ve `main` ile birleştirilmez.

## Plan ve veri sözleşmeleri

- [MVP ve aşamalı yapılacaklar listesi](docs/mvp-roadmap.md)
- [Başlangıç varsayımları](docs/assumptions.md)
- [Teknolojiler](docs/technologies.md)
- [Kodlama kuralları](docs/coding-conventions.md)

Graphify çıktıları `graphify-out/` altında yerel tutulur ve Git tarafından yok sayılır.

## Task 2: Arazi ufku

Task 2, önbellekteki tam NASA arazi dosyasından Ay eğriliğini ve gözlemci
yüksekliğini içeren 360° ufuk profili hesaplar. Backend veriyi salt okunur kullanır.
Eksik yönler `null` olarak raporlanır; arama mesafesinin dışı değerlendirilmez.

```bash
docker compose --env-file .env.example build backend
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.compute_horizon \
  terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29 \
  > horizon.json
```

Örnekteki referans Task 1'in Malapert isteğine aittir; farklıysa kendi `ready`
yanıtındaki `terrain_path` değerini kullan. Varsayılanlar 1° yön aralığı, 100 km
arama mesafesi ve uzakta 40 m örneklemedir; ilk 1 km her metrede örneklenir.
80 m arazi çözünürlüğü değişmez. Bu profil henüz Güneş/Dünya görünürlük süresi
veya güvenli iniş kararı üretmez. Görünürlük Task 3, arayüz Task 4 kapsamındadır.

[Hesaplama ayrıntıları, sınırlamalar ve doğrulama](docs/terrain-horizon.md).
Task 2 yalnızca `feature/task2-terrain-horizon` branch'ine gönderilir;
`tahakrcc` ve `main` ile birleştirmeyi kullanıcı yapar.

## Task 3: Görünürlük ve süre metrikleri

Task 1'in `ready` yanıtındaki iki referansla çalıştır:

```bash
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.analyze_visibility TERRAIN_REFERENCE HORIZONS_REFERENCE --refine \
  > visibility.json
```

Güneş için tam/kısmi görünürlük ve karanlık, Dünya için merkez görünürlüğü;
toplam süre/yüzde, en uzun kesinti ve kesintisiz görünürlük, sonraki batış
hesaplanır. Eksik veri ayrıca raporlanır. `--refine`, bulunan geçişler için
RabbitMQ üzerinden 5 dakikalık ve ardından 1 dakikalık veri ister; servisler
çalışıyor olmalıdır. Bu seçenek olmadan yalnızca yerel veriler okunur.

Süreler örnekler arasındaki modele göre tahmindir. Geçişlerin zaman aralığı ve
arazi belirsizliği sonuçta korunur. Dünya'nın görünmesi gerçek bir haberleşme
bağlantısı garantisi değildir. [Model, komutlar ve doğrulama](docs/visibility-metrics.md).
Task 3 yalnızca `feature/task3-visibility-metrics` branch'ine gönderilir;
birleştirmeyi kullanıcı yapar.


## Task 4: Analiz API’si ve 3D arayüz

Takımın 5173 portundaki ortamına dokunmadan yeni önizleme:

```bash
docker compose -p nasa-task4 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.preview.yml up -d --build --wait
```

- Arayüz: http://localhost:5174
- API: http://localhost:8001/docs
- `Plan your observation` doğrudan analiz paneline geçer. `Run the Malapert example` gerçek veriyle örnek analiz başlatır.
- NASA LRO ve Apollo modelleriyle kaydırmalı giriş temsili bir animasyondur; fiziksel iniş yörüngesi hesabı değildir.
- Nokta/koordinat, arazi üstü gözlemci yüksekliği, UTC başlangıç ve süre seçilir. Sonuçta ufuk, Güneş/Dünya konumları, görünürlük aralıkları ve sonraki batış gösterilir.
- Zaman çubuğu ve oynatma örnekler arasında ilerler. Detaylandırılmış örneklerin zaman aralıkları eşit değildir.
- İşler ayrı `analysis-state` diskinde kalır; tarayıcı son iş kimliğini saklar. Backend yeniden başladığında tamamlanmış sonuç korunur, yarım işler kuyruğa geri alınır.
- İlk boş veri diskiyle çalışma NASA’dan arazi indirir; bağlantı gerekir. Backend veri diskine yazmaz, indirmeler RabbitMQ üzerinden ingestion servisine gider.

[API sözleşmeleri, kaynaklar ve kullanım](docs/analysis-api-and-interface.md) · [Task 4 doğrulama raporu](docs/verification/task4-interface-2026-11-14.json).

Task 4 yalnızca `feature/task4-mission-interface` dalına gönderilir; tahakrcc/main ile birleştirme kullanıcıya aittir. İki senaryoyu karşılaştırma Task 5 kapsamındadır.


## Task 5: A/B konum ve tarih karşılaştırması

Tek nokta analizinin altındaki **Add comparison** düğmesiyle mevcut sonucu A olarak koruyun. B için aynı yerde farklı tarih veya aynı tarihte farklı konum seçin. **Use +48-hour delay** hazır gecikme örneğini kurar; **Run scenario B** gerçek analiz başlatır.

Ortak süre/yükseklik/hesaplama ayarları korunur. Güneşlenme, en uzun karanlık ve Dünya görünürlüğü farkları B−A olarak gösterilir. Ortak zaman kontrolünde iki UTC tarihi ve gerçekten hesaplanmış örneklerin zamanı açıkça yazılır. Masaüstünde yan yana, mobilde A/B sekmeleriyle kullanılır. B taslağı ve iş kimliği tarayıcıda saklanır; A değişmez.

`POST /comparisons` iki tamamlanmış iş kimliği alır. Uyumsuz koşulları reddeder; eksik kapsamı sıfır görünürlük gibi yorumlamaz. Task 5 doğrulamasında **212 test** (128 backend, 75 ingestion, 9 arayüz), lint, derleme ve npm audit geçti.

[Kullanım, API ve model sınırları](docs/scenario-comparison.md) · [Gerçek veri doğrulaması](docs/verification/task5-comparison-2026-11-14.json).

Bu çalışma yalnızca `feature/task5-scenario-comparison` dalına gönderilir; tahakrcc ve main ile birleştirilmez.


## Task 6: Doğrulama ve demo hazırlığı

Teknik kontroller tamamlandı: **222 test** (136 backend, 75 ingestion, 11 arayüz), önbelleksiz Docker kurulumu, gerçek ilk indirme/önbellek karşılaştırması, hata/toparlanma ve üretim arayüzü kontrol edildi.

Düzeltilenler: boş tarih alanında hazır örneğin başlatılması, sayfa yenilendiğinde kayıtlı analiz girdilerinin forma geri gelmesi ve RabbitMQ hazır olmadan başlayan backend bağlantısının sınırlı tekrar denemesi.

Bu oturumda ilk 24 saatlik analiz yaklaşık **268 sn**, önbellekten aynı analiz **4 sn**, 30 günlük detaylandırılmış analiz **58 sn** sürdü. Bunlar yerel ölçümlerdir; sunumdan önce örnekleri hazırlayın.

[Doğrulama raporu](docs/mvp-verification.md) · [Demo akışı ve takım arkadaşı kontrol listesi](docs/demo-runbook.md).

**Takım arkadaşının kullanım değerlendirmesi hâlâ bekliyor; tüm MVP yayın kapısı kapanmış sayılmıyor.** Task 6 yalnızca `feature/task6-demo-verification` dalına gönderilir; tahakrcc/main ile birleştirilmez.

## Task 7: Uygun başlangıç zamanı arama

Üst menüde **Time search** seçin. Konum, UTC başlangıç aralığı, görev süresi ve
adaylar arasındaki saat farkını; ardından minimum güneşlenme yüzdesi, maksimum
kesintisiz karanlık ve minimum Dünya görünürlüğünü girin. En fazla 16 aday hesaplanır.

Her adayın sağladığı/sağlamadığı koşullar gösterilir; eksik veri uygun sonuç sayılmaz.
**Open analysis** sonucu açar, **Use as comparison A** karşılaştırmanın A senaryosu
yapar. Arama ilerlemesi yeniden başlatmalarda korunur.

Hazır güneş örneği Dünya şartını açıkça 0 saate indirir; 4–6 Kasım 2026 Malapert
başlangıçlarında yalnızca 6 Kasım güneş koşullarını sağlar. Dünya şartı 4 saatse
bu üç adayın hiçbiri uygun çıkmaz. Aradaki bütün anlar taranmaz; kısa fırsatlar kaçabilir.

**243 test**, Ruff, derleme ve npm audit geçti. [Kullanım ve API](docs/window-search.md).
Task 7 yalnızca `feature/task7-window-search` dalına gönderilir; tahakrcc/main ile birleştirilmez.

## R1: Dışa aktarma, paylaşım ve sınıf modu

- Sonuçların yanındaki **Export / Share** panelinden CSV, grafik PNG, yazdırılabilir HTML raporu ve senaryo JSON dosyası indirilir. HTML raporunu açıp tarayıcıdan **Yazdır / PDF olarak kaydet** seçilebilir.
- Bağlantı, aynı sunucudaki kayıtlı analizi ve seçili zamanı açar; A/B karşılaştırması da paylaşılabilir. `localhost` bağlantısı başka bilgisayardan senin sunucuna erişim sağlamaz.
- **Mission control → Import a shared scenario file** girdileri ve beklenen veri/model kimliklerini yükler. **Calculate imported scenario** yeniden hesaplar; sonuç eski parmak iziyle eşleşmiyorsa açıkça uyarılır. JSON dosyası NASA veri önbelleğini içermez.
- Zaman aramasında hiç uygun sonuç yoksa en yakın üç değerlendirilebilir aday ve her koşuldaki eksik/fazla miktar gösterilir. Eksik verili adaylar sıralanmaz.
- **Classroom → Start lesson** iki tıkla Malapert örneği açar. 4 ve 6 Kasım örnekleri; hız seçimi, oynatma/durdurma, zaman çubuğu ve açıklama kartları bulunur. Bu mod bir gözlemcinin ufuk/görünürlüğünü öğretir; yüzey geneli hareketli gölge simülasyonu veya kalıcı gölge doğrulaması yapmaz.

**263 test** ve derleme kontrolleri geçti. [R1 kullanım, API ve kapsam](docs/release-r1.md).
Yalnızca `feature/r1-exports-sharing-classroom` dalına gönderilir; tahakrcc/main ile birleştirme kullanıcıya aittir. Takım arkadaşı MVP kabulü hâlâ bekliyor.

## R2: DSN yer istasyonlarıyla Dünya'ya doğrudan iletişim

Görev kontrolündeki **Direct-to-Earth communication** paneli, kaydedilmiş bir analiz için Dünya ile doğrudan iletişimin geometrik olarak mümkün olduğu zamanları gösterir. Bir pencere için iki koşulun aynı anda sağlanması gerekir:

- Dünya'nın merkezi iniş noktasındaki arazi ufkunun üstünde olmalı.
- Ay, NASA DSN istasyonlarından en az birinde (Goldstone DSS-14, Canberra DSS-43, Madrid DSS-63) yükseklik sınırının üstünde olmalı.

Varsayılan sınır 6° (alma), alternatifi 10° (düşük güçlü gönderme). İstasyon koordinatları NASA DSN 810-005 modül 301 Rev. P'den, Ay konumları JPL Horizons'tan gelir. İstasyon ufuk maskeleri, takvim ve link-budget modellenmez; sonuç bir bağlantı garantisi değildir. [Kaynaklar, yöntem ve API](docs/ground-station-communication.md).

## R3: Açıklamalı iniş noktası önerisi

Gezinmedeki **Recommend** bölümü 2–6 aday noktayı aynı UTC zaman aralığında sıralar. Kullanılan kriterler güneş ışığı, en uzun karanlık, DSN iletişimi (R2) ve arazi güvenliğidir. Her sıranın kriter katkıları ve kısa bir açıklaması gösterilir. Elenen her noktanın hangi kesin koşulu hangi değerle geçemediği yazılır. Verisi eksik noktalar sıralanmaz, ayrıca gösterilir.

Ağırlık kaydırıcıları değişince sıralama, analizler tekrar çalışmadan yeniden hesaplanır; eski ve yeni sıralama yan yana gösterilir. Takvim kaydığında (örneğin 48 saat) aynı adaylar yeni tarih için yeniden sıralanır ve en iyi alternatif önerilir.

Eğim, önbellekteki 80 m LOLA verisinden Horn yöntemiyle hesaplanır. Varsayılan 10° iniş alanı sınırı bir planlama varsayımıdır; NASA sayısal bir değer yayımlamamıştır. 80 m çözünürlük küçük krater ve kayaları göremez; yakıt ve bilim değeri henüz modellenmez. [Yöntem, kaynaklar ve API](docs/recommendations.md).

## R4: İniş yörüngesi

Gezinmedeki **Descent** bölümü, seçilen iniş noktası için Ay yörüngesinden temasa kadar iniş yörüngesini hesaplar. Aşamalar: yörüngeden iniş manevrası (DOI), süzülme, frenleme, yaklaşma ve son iniş. Her aşamanın süresi, Δv'si ve yakıtı gösterilir; irtifa–menzil grafiği üzerinde fareyle anlık değerler okunabilir.

Varsayılan araç, Apollo 11 Ay modülüdür. Değerler NASA TN D-6846 ve Apollo 11 görev raporundan alınmıştır. Model, Apollo 11 iniş planındaki toplam Δv'yi (6775 fps) %2 farkla tutturur. Yörünge iniş noktasının üzerinden geçmiyorsa gereken düzlem değişimi bildirilir; yakıt yetmezse ya da temas toleransları aşılırsa nokta "ulaşılamaz" sayılır ve nedeni yazılır.

Arazi engelleri, Ay'ın kütle konsantrasyonları (mascon) ve dönüşü modellenmez. Görselleştirme 2B'dir. [Kaynaklar, model ve doğrulama](docs/descent-trajectories.md).

## R5: Rotalar arası yakıt optimizasyonu

**Descent → Optimize routes**, aynı iniş noktası için 27 rota üretir. Rotalar üç değişkenle çeşitlenir: başlangıç yörüngesi (40/60/80 deniz mili), yaklaşma süresi (60/100/140 s) ve son iniş süresi (60/108/150 s). Her rotanın yörüngeden iniş manevrasından (DOI) temasa kadar harcadığı yakıt hesaplanır.

Varsayılan temas rezervi 145 fps'dir. Bu değer NASA TN D-6846 Tablo II'deki iniş noktası değiştirme (60 fps) ve elle hover (85 fps) paylarının toplamıdır. Rezervi, yakıtı ya da R4 sınırlarını aşan rotalar nedeniyle birlikte elenir. Grafik, harcanan yakıtı inceleme süresine (yaklaşma + son iniş) karşı gösterir; Pareto cephesi ve en az yakıt harcayan rota işaretlidir.

**Recommend** bölümünde "include descent" seçeneği açılırsa, her aday için R4 inişi hesaplanır. Ulaşılamayan nokta elenir; yakıt payı 5. kriter olarak sıralamaya eklenir. Seçenek kapalıyken R3 sonuçları değişmez. [Yöntem, kaynaklar ve doğrulama](docs/fuel-optimization.md).

## H0: Hibrit planlama için ortak veri temeli

Jupyter notebook: [`notebooks/H0_shared_contracts.ipynb`](notebooks/H0_shared_contracts.ipynb). Ortak modeller, beş örnek senaryo, veri doğrulama ve girdi parmak izi kontrollerini hücre hücre çalıştırır. Kurulum ve alan açıklamaları: [H0 sözleşmeleri](docs/hybrid-contracts.md). Bu aşamada kuantum çözücü çalıştırılmaz; klasik referans H1, QUBO/QAOA deneyi H2 kapsamında geliştirilir.


## H1: Klasik çoklu görev planlaması

[`notebooks/H1_classical_planning.ipynb`](notebooks/H1_classical_planning.ipynb), aynı Jupyter çekirdeğiyle bağımsız seçimleri ortak planlamayla karşılaştırır. Üç görev ve dört seçenek için 64 birleşimi değerlendirir; kapasite, zaman ve enerji ihlallerini açıklar. Tek kanallı örnekte 24 geçerli plan arasından 26 ham bilim puanlı planı seçer. Eksik bilgi, çözümsüzlük ve yarıda kalan arama ayrı gösterilir.

[Yöntem, sınırlar ve testler](docs/classical-planning.md). Bu aşama statik klasik referanstır; kuantum devresi veya gerçek iniş güvenliği değerlendirmesi çalıştırmaz. Hibrit geliştirme `codex/quantum-hybrid` branch'inde sürdürülür.


## H2: Küçük kuantum simülatörü deneyi

[`notebooks/H2_quantum_mapping.ipynb`](notebooks/H2_quantum_mapping.ipynb), 12 seçim değişkenli QUBO modelini klasik referansla doğrular ve aynı üç görevli problemi 6 kubitlik QAOA simülatöründe dener. Tek katman düşük kapı sayılı varsayılandır; iki katmanla sonuç ve kapı ihtiyacını karşılaştırır. Ham geçersiz örnekler gizlenmez, klasik onarım yapılmaz.

[Yöntem, ölçümler ve sınırlar](docs/quantum-mapping.md). Gerçek kuantum servisine iş göndermez; bu küçük örnek klasik yöntemle zaten kolayca çözülür. Simülatörde örnek başına olasılığın artması, klasik çözücüye göre hız veya maliyet üstünlüğü anlamına gelmez.


## H3: İletişim takvimi ve enerji entegrasyonu

[`notebooks/H3_resource_integration.ipynb`](notebooks/H3_resource_integration.ipynb), geometrik görüşü istasyon tahsisinden ayırır; faaliyet tüketimini başlangıç enerjisi, kapasite, rezerv ve üretimle birlikte değerlendirir. Dolu bataryada depolanamayan üretim ve sonradan gelen enerji yanlışlıkla kullanılabilir sayılmaz. Desteklenen örnekte klasik ve kuantum çözücü aynı girdiyi kullanır; kuantum kodlamasına sığmayan kısıtlar açıkça bildirilir.

[Varsayımlar, yöntem ve doğrulama](docs/resource-integration.md). Notebook verileri sentetiktir; gerçek DSN tahsisi, radyo bağlantı garantisi veya fiziksel batarya doğrulaması değildir.

## H8 — Klasik ve kuantum karşılaştırması

Görev kontrolündeki **Çözücü arenası** sekmesi (`#/solver-arena`), aynı örnek senaryoda klasik tam arama ile yerel QAOA simülasyonunu karşılaştırır. Gerçek hesap adımlarını oynatabilir, ham ölçümleri ve doğrulanmış planları inceleyebilir, sonuçları JSON olarak indirebilirsin. H7'nin kayıtlı Rigetti deneyi ayrı gösterilir; yeni kuantum işi gönderilmez ve kredi harcanmaz.

- Açıklama ve sınırlar: [H8 solver arena](docs/solver-arena.md)
- Çalıştırılmış notebook: [H8_solver_arena.ipynb](notebooks/H8_solver_arena.ipynb)
