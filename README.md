# Ay İniş Alanı Navigatörü

2gbram takımının NASA Space Apps Challenge projesi. İlk MVP, Ay'daki konum ve tarih seçeneklerini Güneş/Dünya görünürlüğüne göre karşılaştırır.

## Mevcut durum

Step 0 servis iskeleti yerel Docker ortamında doğrulandı. Backend sağlık endpoint'i, ortak veri modelleri ve başlangıç arayüzü mevcut. Horizons Güneş/Dünya verileri ingestion komutuyla indirilip önbelleğe alınabilir. Arazi ve görünürlük hesaplamaları sonraki adımlardadır.

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

Worker bu aşamada RabbitMQ bağlantısını açıp bekler. İndirme işlerini işleme Step 1'de eklenecek. Backend kaynak değişikliklerini yeniden yükler. Worker değişikliklerinde `docker compose restart data-ingestion` kullanın.

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

Doğrudan bağımlılıklar sabitlendi. Frontend Vite 7.3.8 ve Vitest 4.1.12 kullanır; kilit dosyası güncellendi ve `npm ci` ile doğrulandı. 14 Kasım 2026 kontrolünde `npm audit` sıfır açık bildirdi; 33 test, Python lint kontrolleri ve frontend derlemesi geçti. Python geçişli bağımlılıklarının kilitlenmesi bekliyor.

```bash
docker compose run --rm --no-deps frontend npm ci
```

## Horizons verilerini indirme

```bash
docker compose --env-file .env.example run --rm --no-deps data-ingestion \
  python -m app.download_horizons examples/horizons-reference.json
```

Referans noktasında 14–15 Kasım 2026 için saatlik 25 örnek indirildi; tekrar çalıştırma önbelleği kullandı. Ham yanıtlar, kaynak bilgileri ve eşleştirilmiş Güneş/Dünya verileri ortak `/data/horizons/` dizininde saklanır. Uzun süre çalışan worker için kuyruk tüketicisi henüz eklenmedi; bu aşama CLI üzerinden çalışır. [Ayrıntılar ve doğrulama](docs/horizons-ingestion.md).

## Plan ve veri sözleşmeleri

- [MVP ve aşamalı yapılacaklar listesi](docs/mvp-roadmap.md)
- [Başlangıç varsayımları](docs/assumptions.md)
- [Teknolojiler](docs/technologies.md)
- [Kodlama kuralları](docs/coding-conventions.md)

Graphify çıktıları `graphify-out/` altında yerel tutulur ve Git tarafından yok sayılır.
