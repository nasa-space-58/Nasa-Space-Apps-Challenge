# Ay İniş Alanı Navigatörü

2gbram takımının NASA Space Apps Challenge projesi. İlk MVP, Ay'daki konum ve tarih seçeneklerini Güneş/Dünya görünürlüğüne göre karşılaştırır.

## Mevcut durum

Task 0 tamamlandı: servis kurulumu, bağımlılık kilitleri, temiz kurulum ve başlangıç arazi/nokta kaynakları doğrulandı. Backend sağlık endpoint'i, ortak veri modelleri ve başlangıç arayüzü mevcut. Task 1 bu dalda uygulandı: RabbitMQ istekleri Güneş/Dünya verisi ve kalıcı LOLA arazi kesiti üretir. Task 2 arazi ufkunu, Task 3 görünürlük aralıklarını ve süre metriklerini hesaplar. Kullanıcı arayüzü ve analiz API'si Task 4 kapsamındadır.

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

Doğrudan bağımlılıklar sabitlendi. Frontend Vite 7.3.8 ve Vitest 4.1.12 kullanır; kilit dosyası güncellendi ve `npm ci` ile doğrulandı. 14 Kasım 2026 kontrolünde `npm audit` sıfır açık bildirdi; 33 test, Python lint kontrolleri ve frontend derlemesi geçti. Python geçişli bağımlılıkları `requirements.lock` dosyalarında sürüm ve SHA-256 hash ile kilitlendi; Docker kurulumu `--require-hashes` ve `pip check` kullanır. [Kilitleri güncelleme](docs/python-dependencies.md).

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

Başlangıç karşılaştırma noktaları Mons Mouton merkezi ve Malapert Massif R23 olarak kaynaklandırıldı. İki noktanın çevresindeki DEM pencerelerinde veri kapsamı doğrulandı. Bunlar güvenli iniş noktası sertifikası değildir; uygulama içi nokta seçimi ve arazi ufku hesabı henüz eklenmedi. [Arazi ve koordinat kaynakları](docs/terrain-and-sites.md).

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
