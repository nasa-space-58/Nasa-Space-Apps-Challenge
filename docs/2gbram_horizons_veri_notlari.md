# 2gbram · Ay İniş Alanı Navigatörü

## NASA/JPL Horizons veri ve sorgu notları

Amaç, Ay'ın güney kutbundaki aday noktalar için Güneş'in ve Dünya'nın yerel ufka göre konumlarını hesaplamak ve görünürlük pencerelerini karşılaştırmak.

## 1. Örnek API istekleri

- [Güneş · COMMAND=10](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%2710%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
- [Dünya · COMMAND=399](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%27399%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)

| Parametre | Değer | Açıklama |
|---|---|---|
| `COMMAND` | `10` veya `399` | Güneş ve Dünya merkezi |
| `EPHEM_TYPE` | `OBSERVER` | Gözlemci efemerisi |
| `CENTER` | `coord@301` | Ay üzerinde kullanıcı tanımlı gözlemci |
| `COORD_TYPE` | `GEODETIC` | Jeodezik koordinat |
| `SITE_COORD` | `0,-89.5,0` | Doğu boylamı ve enlem derece, gözlemci yüksekliği km |
| `START_TIME` | `2026-11-14 00:00` | UTC başlangıç |
| `STOP_TIME` | `2026-11-15 00:00` | UTC bitiş |
| `STEP_SIZE` | `1h` | Saatlik örnekleme |
| `QUANTITIES` | `4` | Görünen azimut ve yükseklik |
| `APPARENT` | `AIRLESS` | Atmosferik kırılma kapalı |
| `ELEV_CUT`, `SKIP_DAYLT` | `-90`, `NO` | Ufuk altı ve gündüz örnekleri dahil |
| `format`, `CSV_FORMAT`, `EXTRA_PREC` | `text`, `YES`, `YES` | Metin içinde CSV tablo ve ek basamaklar |
| `MAKE_EPHEM`, `OBJ_DATA` | `YES`, `NO` | Efemeris tablosu açık, nesne özellikleri özeti kapalı |

Azimut kuzeyden saat yönünde ölçülür. Kuzey 0°, doğu 90°, güney 180°, batı 270°. Yükseklik yerel zenite dik düzleme göre ölçülür. Görünen konum, ışık yolculuk süresi ve aberasyon gibi düzeltmeleri içerir. Canlı yanıtta `UT` etiketi bu tarihler için UTC anlamına gelir.

## 2. Canlı API kontrol değerleri

Örnek nokta 0° doğu, 89,5° güney ve 0 km yüksekliktedir. Bu koordinat sorgu testi için seçildi. Horizons yanıtında efemeris `DE441`, Ay yönelim modeli `MEAN_ME (high precision)` ve referans küre yarıçapı 1737,4 km olarak bildirildi.

| Hedef | 14 Kasım 2026 00.00 UTC azimut | Aynı anda yükseklik | 25 örnekte yükseklik aralığı |
|---|---:|---:|---:|
| Güneş | 126.627333° | +1.258335° | +1.25833° ile +1.34951° |
| Dünya | 358.773423° | −4.148545° | −4.14855° ile −3.05352° |

Her hedef için 25 örnek, aralarında 24 saatlik analiz süresi oluşturur. Saatlik örneklerden çıkarılan referans ufku sonucu Güneş merkezi için 24 saat, Dünya merkezi için 0 saattir. Arazi engelleri bu hesapta yer almıyor.

## 3. Normalize edilmiş veri şeması

Benzersiz satır anahtarı `(site_id, sampled_at_utc)`. Güneş ve Dünya tablolarını UTC zaman damgasına göre eşleştirin.

| Alan | Tip ve birim | Açıklama |
|---|---|---|
| `site_id` | Metin | Adayın sabit kimliği |
| `longitude_deg_east` | Ondalık derece | Doğu pozitif, [0, 360) |
| `latitude_deg` | Ondalık derece | Güney negatif, [−90, 90] |
| `altitude_km` | Ondalık km | Referans yüzeyine göre gözlemci yüksekliği |
| `sampled_at_utc` | ISO 8601 UTC | Örnek `2026-11-14T00:00:00Z` |
| `sun_azimuth_deg`, `earth_azimuth_deg` | Ondalık derece veya null | [0, 360) |
| `sun_elevation_deg`, `earth_elevation_deg` | Ondalık derece veya null | [−90, 90]. Negatif değerleri koruyun. |
| `source` | Metin | NASA/JPL Horizons |
| `sun_request_id`, `earth_request_id` | Metin | Yeniden üretim için ham sorgu kayıtları |

- `$$SOE` ve `$$EOE` arasındaki satırları okuyun. Zaman damgası ile açı sütunları arasında ek işaret sütunları bulunur. Açıları CSV başlığındaki `Azimuth_(a-app)` ve `Elevation_(a-app)` alanlarından alın.
- HTTP 200 yanıtında da Horizons hatası bulunabilir. Hedefi, merkezi, zaman ölçeğini, zaman dizisini ve beklenen satır sayısını doğrulayın. Eksik veya `n.a.` değerlerini bilinmiyor olarak işaretleyin.
- Tam istek URL'sini, ham yanıtı, çekim zamanını, API sürümünü ve efemeris ile yönelim başlıklarını saklayın.

## 4. Görünürlük pencereleri

1. **Referans ufku.** Açıklık açısı hedef yüksekliği eksi 0° olarak hesaplanır. Pozitif açıklık merkez görünürlüğünü, negatif açıklık ufuk altını, sıfır sınırı gösterir.
2. **Arazi ufku.** LOLA sayısal yükseklik modelinden (DEM) her aday ve gözlemci yüksekliği için azimuta bağlı bir ufuk profili çıkarın. Ay'ın eğriliğiyle birlikte her doğrultuda en yüksek engel açısını hesaplayın. Hedef yüksekliğinden ilgili engel açısını çıkarın. Azimutun 359°–0° geçişinde dairesel enterpolasyon uygulayın. Kapsama yetersizse sonucu bilinmiyor işaretleyin.
3. **Geçiş zamanı.** Açıklık işaret değiştirirse geçişi doğrusal enterpolasyonla tahmin edin. Sınıra yakın bölgeleri 5 dakika, gerekirse 1 dakika aralıkla yeniden sorgulayın. İki ardışık saatlik örnek arasında birden çok geçiş yaşanabilir. Dar engellerde analiz aralığını daha sık örnekleyin.
4. **Pencere alanları.** `site_id`, `target`, `start_utc`, `end_utc`, `duration_s`, `horizon_model`, `refinement_step_s` ve `quality_status`. Analiz sınırına dayanan pencereleri kırpılmış olarak işaretleyin. Eksik veri üzerinden pencere birleştirmeyin.
5. **Aday karşılaştırması.** Aynı analiz döneminde toplam görünür süre, görünürlük yüzdesi, en uzun kesintisiz pencere, en uzun kesinti ve bilinmeyen süreyi raporlayın.

## 5. Hesaplama sınırları

- **Güneş enerjisi.** Merkez görünürlüğü enerjiye erişim potansiyelini gösterir. Gerçek üretim için panel yönelimi, geliş açısı, panel verimi ve tutulmalar ayrıca değerlendirilir. Kısmi Güneş diski görünürlüğü için `QUANTITIES='4,13'` ile açısal çap alınabilir. Çapı yay saniyesinden dereceye çevirip ikiye bölün. Ufuk yüksekliği disk boyunca yaklaşık sabitse merkez yüksekliği artı yarıçap kısmi görünürlüğü, merkez yüksekliği eksi yarıçap tam görünürlüğü kontrol etmek için kullanılır.
- **Dünya haberleşmesi.** `COMMAND=399` Dünya merkezini temsil eder. Gerçek bağlantı ayrıca seçilen istasyonun konumu ve müsaitliği, iki uçtan görüş, minimum yükseklik açısı, anten yönelimi ve bağlantı bütçesine bağlıdır.
- **Arazi doğruluğu.** DEM çözünürlüğü, kot belirsizliği, kapsama ve panel ya da anten yüksekliği ufka yakın geçişleri etkiler. DEM ile Horizons koordinat çerçevesini ve yükseklik referansını eşleştirin. API'nin gösterdiği ondalık basamak sayısı pencerenin doğruluğunu belirlemez.

## 6. Kaynaklar ve yarışma

### Görevin kaynak sekmesindeki araçlar

| Kaynak | Projede kullanım |
|---|---|
| [LROC QuickMap](https://quickmap.lroc.im-ldi.com/) | Ay'ın güney kutbundaki aday iniş alanlarını LRO görüntüleri ve topografya katmanlarıyla incelemek. |
| [NASA Moon Trek](https://trek.nasa.gov/moon/) | Aday noktaları ve çevredeki yükseltileri 3B görselleştirerek araziyi ve yerel ufku yorumlamak. |
| [NASA/JPL Horizons System](https://ssd.jpl.nasa.gov/horizons/) | Her aday nokta için Güneş ve Dünya'nın zamana bağlı azimut ve yükseklik açılarını almak. Bu açıları arazi ufkuyla karşılaştırarak güneş enerjisi ve doğrudan Dünya iletişimi için görünürlük pencerelerini hesaplamak. |
| [Kanada Uzay Ajansı (CSA) · Ay Keşfi](https://www.asc-csa.gc.ca/eng/astronomy/moon-exploration/) | Ay keşif görevleri, Kanada'nın katkıları ve Artemis programı hakkında arka plan bilgisi edinmek. |

### Teknik belgeler ve yarışma referansları

- [NASA Lunar Landing Site Navigator görev sayfası](https://www.spaceappschallenge.org/2026/challenges/lunar-landing-site-navigator/)
- [NASA/JPL Horizons API belgeleri](https://ssd-api.jpl.nasa.gov/doc/horizons.html)
- [NASA/JPL Horizons kullanım kılavuzu](https://ssd.jpl.nasa.gov/horizons/manual.html)
- [NASA Güney Kutbu LOLA yükseklik haritaları](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/)
- [NASA Güney Kutbu LOLA DEM mozaiği](https://pgda.gsfc.nasa.gov/products/81)
- [HackAthlone görev kaynakları](https://hackathlone.com/challenges/lunar-landing-site-navigator)
- [NASA Space Apps GO Team açıklaması](https://github.com/nasa/spaceapps/discussions/1827)

HackAthlone görev kopyasında Horizons System, NASA kaynakları arasında listeleniyor. Önceki incelemede NASA görev sayfasının Resources sekmesi doğrudan doğrulanamadı. GO Team açıklamasına göre Global Awards için görevin Resources listesinde bulunan en az bir NASA veri kaynağı kullanılmalı ve kullanılan kaynaklar teslimde belirtilmeli. Açıklama, geliştirmenin yerel etkinlik saatiyle 14 Kasım 09.00'da başladığını ve öncesinde araştırmaya izin verildiğini belirtiyor.
