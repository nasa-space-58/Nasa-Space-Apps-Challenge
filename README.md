# Ay İniş Alanı Navigatörü

2gbram takımının NASA Space Apps Challenge projesi. İlk MVP, Ay'daki konum ve tarih seçeneklerini Güneş/Dünya görünürlüğüne göre karşılaştırır.

## Mevcut durum

Task 0 tamamlandı: servis kurulumu, bağımlılık kilitleri, temiz kurulum ve başlangıç arazi/nokta kaynakları doğrulandı. Backend sağlık endpoint'i, ortak veri modelleri ve başlangıç arayüzü mevcut. Task 1 bu dalda uygulandı: RabbitMQ istekleri Güneş/Dünya verisi ve kalıcı LOLA arazi kesiti üretir. Ufuk ve görünürlük hesaplamaları sonraki adımlardadır.

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
