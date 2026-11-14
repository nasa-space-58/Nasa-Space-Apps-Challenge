# Kaynaklar — Lunar Landing Site Navigator

> Görev: **[Lunar Landing Site Navigator](https://www.spaceappschallenge.org/2026/challenges/lunar-landing-site-navigator/)** — NASA Space Apps Challenge 2026 (Sivas yerel etkinliği, 14–15 Kasım 2026)
>
> Bu dosya, görevin resmi kaynaklarını ve araştırılan ek API / veri setlerini **projeye kattığı güce göre** sıralar.
> Son kontrol: 14 Kasım 2026. ✅ = sayfası açılıp doğrulandı · 🔎 = arama sonucundan, açıp kontrol etmek gerekir · 🧪 = ekip tarafından canlı API ile test edildi.
>
> Horizons sorgu ayrıntıları, veri şeması ve görünürlük penceresi algoritması [2gbram_horizons_veri_notlari.md](2gbram_horizons_veri_notlari.md) dosyasından bu dosyaya özetlenmiştir; ayrıntı için o dosyaya bakın.

## ⚠️ Yarışma kuralı (kaynak kullanımı)

[NASA Space Apps GO Team açıklamasına](https://github.com/nasa/spaceapps/discussions/1827) göre **Global Awards** için görevin **Resources** listesindeki **en az bir NASA veri kaynağı kullanılmalı ve teslimde belirtilmeli**. Bizim için bu kaynak **JPL Horizons** (resmi listede). Açıklamaya göre geliştirme yerel saatle **14 Kasım 09.00**'da başladı; öncesinde yalnızca araştırmaya izin verildi.

---

## Görevin istediği, tek cümleyle

Seçilen **iniş yeri + tarih** için **Güneş'in ve Dünya'nın ufka göre konumunu** göstermek ve buradan **güç (güneş enerjisi)** ve **doğrudan Dünya iletişimi** pencerelerini çıkarmak; birden fazla yer/tarihi hızlıca karşılaştırmak.

Bunun için üç tür veri lazım:

| İhtiyaç | Ne sağlar | En iyi kaynak |
|---|---|---|
| **Gök konumu** | Güneş ve Dünya'nın azimut + yükseklik açısı (zamana göre) | JPL Horizons / Skyfield + SPICE |
| **Arazi** | Etraftaki dağ/krater yükseklikleri → gerçek ufuk | LOLA yükseklik modelleri (PGDA) |
| **Yer listesi** | Apollo, CLPS, Artemis, diğer ülkelerin iniş yerleri + koordinatları | NASA CLPS sayfası, Artemis duyuruları, USGS |

---

## Güç sıralaması

| Sıra | Kaynak | Rol | Güç | Resmi kaynak listesinde mi? |
|---|---|---|---|---|
| 1 | JPL Horizons API | Güneş/Dünya konumu | ⭐⭐⭐⭐⭐ | ✅ Evet |
| 2 | PGDA – LOLA Güney Kutbu DEM'leri | Gerçek ufuk hesabı | ⭐⭐⭐⭐⭐ | Hayır (LOLA görevde önerili) |
| 3 | PGDA – Lunar Polar Illumination | Hazır güneş + Dünya görünürlük haritaları | ⭐⭐⭐⭐⭐ | Hayır |
| 4 | Skyfield + NAIF SPICE kernel'ları | Horizons'un çevrimdışı alternatifi | ⭐⭐⭐⭐ | Hayır |
| 5 | Moon Trek WMTS API | 2D harita katmanları (web) | ⭐⭐⭐⭐ | ✅ Evet (Moon Trek) |
| 6 | Cesium Moon Terrain | Hazır 3D Ay küresi + arazi | ⭐⭐⭐⭐ | Hayır |
| 7 | Quickmap (LROC) | Keşif, koordinat bulma | ⭐⭐⭐ | ✅ Evet |
| 8 | İniş yeri listeleri (CLPS, Artemis, Apollo, USGS) | Hazır yer kataloğu | ⭐⭐⭐ | Kısmen |
| 9 | NASA SVS CGI Moon Kit | 3D küre için doku + yükseklik | ⭐⭐⭐ | Hayır |
| 10 | Diğer yüksek çözünürlüklü DEM'ler (PGDA 78/81/104, LROC NAC DTM) | Tek yere yakın çekim | ⭐⭐ | Hayır |
| 11 | CSA Moon Exploration | Sunum / arka plan içeriği | ⭐ | ✅ Evet |

---

## 1. JPL Horizons API ⭐⭐⭐⭐⭐ ✅

- **Ne:** NASA JPL'in gök cisimleri konum hesaplama sistemi. Ay yüzeyinde **kendi tanımladığımız bir noktadan** Güneş ve Dünya'nın **azimut ve yükseklik (AZ & EL)** açılarını, istenen tarih aralığında ve adımda verir. Libration (Ay'ın salınımı) modelin içinde zaten var.
- **Linkler:**
  - API dokümanı: https://ssd-api.jpl.nasa.gov/doc/horizons.html
  - Kılavuz (çıktı tipleri, gözlem noktası tanımı): https://ssd.jpl.nasa.gov/horizons/manual.html
  - Web arayüzü: https://ssd.jpl.nasa.gov/horizons/
- **Uç nokta:** `GET https://ssd.jpl.nasa.gov/api/horizons.api`
- **Parametreler** (🧪 ekip tarafından canlı test edildi):

  | Parametre | Değer | Açıklama |
  |---|---|---|
  | `COMMAND` | `10` / `399` | Güneş / Dünya merkezi |
  | `EPHEM_TYPE` | `OBSERVER` | Gözlemci efemerisi |
  | `CENTER` | `coord@301` | Ay üzerinde kullanıcı tanımlı gözlemci (301 = Ay) |
  | `COORD_TYPE` | `GEODETIC` | Jeodezik koordinat |
  | `SITE_COORD` | `'0,-89.5,0'` | Doğu boylamı (°), enlem (°), yükseklik (km) |
  | `START_TIME` / `STOP_TIME` | `'2026-11-14 00:00'` / `'2026-11-15 00:00'` | UTC |
  | `STEP_SIZE` | `1h` | Saatlik örnekleme |
  | `QUANTITIES` | `4` (kısmi disk için `4,13`) | 4 = Görünen AZ & EL · 13 = hedefin açısal çapı |
  | `APPARENT` | `AIRLESS` | Atmosferik kırılma kapalı (Ay'da atmosfer yok) |
  | `ELEV_CUT` / `SKIP_DAYLT` | `-90` / `NO` | Ufuk altı ve gündüz örnekleri de dahil |
  | `format` / `CSV_FORMAT` / `EXTRA_PREC` | `text` / `YES` / `YES` | Metin içinde CSV tablo, ek basamak |
  | `MAKE_EPHEM` / `OBJ_DATA` | `YES` / `NO` | Efemeris tablosu açık, nesne özeti kapalı |

- **Çalışan örnek sorgular:**
  - [Güneş](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%2710%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
  - [Dünya](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%27399%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
- **🧪 Canlı kontrol değerleri** (0° D, 89,5° G, 0 km · 25 saatlik örnek). Yanıt başlığı: efemeris `DE441`, Ay yönelimi `MEAN_ME (high precision)`, referans küre yarıçapı 1737,4 km.

  | Hedef | 14 Kas 2026 00:00 UTC azimut | Yükseklik | 25 örnekte yükseklik aralığı |
  |---|---:|---:|---:|
  | Güneş | 126.627333° | +1.258335° | +1.25833° … +1.34951° |
  | Dünya | 358.773423° | −4.148545° | −4.14855° … −3.05352° |

  → Düz (referans) ufka göre Güneş merkezi 24 saatin tamamında görünür, Dünya hiç görünmez. Arazi engelleri bu sonuçta **yok**.
- **Açı kuralları:** Azimut kuzeyden saat yönünde (K 0°, D 90°, G 180°, B 270°). Yükseklik yerel zenite dik düzleme göre. Görünen konum ışık yolculuk süresi ve aberasyon düzeltmelerini içerir. Yanıttaki `UT` bu tarihler için UTC'dir.
- **Yanıtı okuma:**
  - Satırlar `$$SOE` ile `$$EOE` arasında. Açıları CSV başlığındaki `Azimuth_(a-app)` ve `Elevation_(a-app)` sütunlarından alın (arada ek işaret sütunları var).
  - **HTTP 200 yanıtında da Horizons hatası olabilir.** Hedefi, merkezi, zaman ölçeğini ve beklenen satır sayısını doğrulayın; `n.a.` değerlerini "bilinmiyor" işaretleyin.
  - Tam istek URL'sini, ham yanıtı, çekim zamanını, API sürümünü ve efemeris/yönelim başlıklarını saklayın (yeniden üretilebilirlik).
- **Uyarılar:**
  - Tarayıcıdan doğrudan çağrılamıyor (CORS engeli — test ettik). → Veriyi **önceden Python ile çekip JSON olarak** uygulamaya koyun ya da küçük bir sunucu/proxy kullanın.
  - Horizons'un "yükseklik açısı" **düz ufka** göredir; dağlar/kraterler hesaba katılmaz. Gerçek ufuk için 2. ve 3. kaynak gerekir.
- **Projede rolü:** Uygulamanın kalbi. Tüm güç/iletişim metrikleri bundan türetilir.

## 2. PGDA – LOLA Güney Kutbu Yükseklik Modelleri (DEM) ⭐⭐⭐⭐⭐ ✅

- **Ne:** NASA Goddard'ın LOLA lazer altimetre verisinden ürettiği güney kutbu yükseklik haritaları. 80–90° Güney için **20 m/piksel**'e kadar; daha geniş bölgeler (75°S, 60°S) daha düşük çözünürlükte.
- **Linkler:**
  - https://pgda.gsfc.nasa.gov/products/90 ("A New View of the Lunar South Pole from LOLA")
  - 5 m/piksel veri klasörü: https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/ (ekip notlarından)
- **Format:** Cloud-Optimized GeoTIFF (COG); güney kutup stereografik X/Y (metre), MOON_ME referans çerçevesi.
- **Örnek dosya:** `LDEM_80S_20MPP_ADJ.TIF` (yüzey yüksekliği, metre). Yanında hata (`_ERR`), eğim vb. katmanlar da var.
- **Projede rolü:** **Ufuk maskesi** hesabı. Her iniş yeri için 360° etrafa bakıp "bu yönde ufuk kaç derece yukarıda?" sorusunun cevabı. Güneş bu açının altındaysa → gölge, Dünya altındaysa → iletişim yok.
- **İpucu:** Tüm dosyayı indirmeyin; COG olduğu için sadece iniş yerlerinin çevresindeki parçayı (`rasterio`/GDAL ile pencere okuma) çekebilirsiniz. Hackathon için 60–120 m/piksel sürümü bile yeterli ve çok daha küçük.

## 3. PGDA – Lunar Polar Illumination (hazır aydınlanma + Dünya görünürlüğü) ⭐⭐⭐⭐⭐ ✅

- **Ne:** LOLA arazisi kullanılarak kutup bölgeleri için hesaplanmış **ortalama güneşlenme** ve **ortalama Dünya görünürlüğü** haritaları (tam bir nutasyon döngüsü boyunca, saatlik adımla).
- **Linkler:**
  - https://pgda.gsfc.nasa.gov/products/69 (Mazarico)
  - 🔎 PDS arşivi (GDRVIS ürünü): https://ode.rsl.wustl.edu/moon/pagehelp/Content/Missions_Instruments/LRO/LOLA/GDR/GDRVIS.htm
  - 🔎 MIT LOLA sunucusu: https://imbrium.mit.edu/BROWSE/EXTRAS/ILLUMINATION
- **Çözünürlük:** 240 m/piksel ve 60 m/piksel (güney + kuzey kutbu).
- **Projede rolü:** Harita üzerinde **"bu bölge genelde ne kadar güneş alıyor / Dünya'yı ne kadar görüyor"** ısı haritası katmanı. Kendi hesabımızı yapmadan, NASA'nın hazır sonucuyla "iyi bölge / kötü bölge" gösterimi. Ayrıca kendi hesaplarımızı **doğrulamak** için mükemmel referans.

## 4. Skyfield + NAIF SPICE kernel'ları ⭐⭐⭐⭐ ✅

- **Ne:** Python astronomi kütüphanesi. Ay yüzeyindeki bir noktadan bakan "astronotun ufkuna göre" Güneş/Dünya yükseklik ve azimutunu **çevrimdışı** hesaplar (`altaz()`).
- **Linkler:**
  - Skyfield gezegen çerçeveleri (Ay'dan bakış örneği dahil): https://rhodesmill.org/skyfield/planetary.html
  - NAIF genel kernel'lar (Ay yönelim dosyaları `pck/` altında): https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/
- **Neden Horizons'a ek:** İnternet/limit derdi yok, binlerce yer × tarih kombinasyonunu saniyeler içinde hesaplar; hackathon sırasında Horizons yavaşlarsa yedek plan.
- **Alternatif:** `spiceypy` (SPICE'ın Python sarmalayıcısı) — daha güçlü ama öğrenmesi daha zor.

## 5. Moon Trek WMTS API ⭐⭐⭐⭐ ✅

- **Ne:** NASA Moon Trek'in harita katmanlarını standart **WMTS karo (tile)** servisi olarak sunan API. LRO görüntü mozaikleri, yükseklik, eğim gibi katmanlar.
- **Link:** https://trek.nasa.gov/tiles/apidoc/trekAPI.html?body=moon
- **URL kalıbı:** `https://{WMTS endpoint}/1.0.0/{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png|jpg`
  Her katmanın `WMTSCapabilities.xml` dosyası, istemci kütüphanesine (Leaflet / OpenLayers / Cesium) verilecek bilgiyi içerir.
- **Projede rolü:** Web uygulamasındaki **2D iniş yeri seçme haritası**. Hiçbir şey indirmeden gerçek NASA görüntüsü.
- **Not:** Kutup bölgeleri için kutup projeksiyonlu katmanları seçin (ekvatoral projeksiyonda kutuplar bozuk görünür).

## 6. Cesium Moon Terrain ⭐⭐⭐⭐ ✅

- **Ne:** Tüm Ay'ın 3D Tiles formatında hazır arazi + görüntü seti. LRO verisinden; **kutuplarda en yüksek çözünürlük** (kutup arazisi 60 m'den 5 m'ye kadar).
- **Linkler:**
  - https://cesium.com/platform/cesium-ion/content/cesium-moon/
  - 🔎 Tanıtım: https://cesium.com/blog/2024/08/06/introducing-cesium-moon-terrain/
- **Erişim:** Ücretsiz Cesium ion hesabı (ticari olmayan / eğitim kullanımı ücretsiz — güncel koşulları kontrol edin). CesiumJS ile tarayıcıda çalışır.
- **Projede rolü:** "Wow" etkisi: iniş yerine 3D yakınlaşma, Güneş ve Dünya'yı gökyüzünde gösterme. Three.js'te sıfırdan arazi kurmaktan çok daha hızlı.
- **Uyarı:** 🔎 Bir kullanıcı, Cesium yüksekliklerinin LOLA'dan ~2,5 km farklı göründüğünü bildirmiş (referans yüzey farkı olabilir). **Ufuk hesabını Cesium'dan değil LOLA DEM'den yapın**; Cesium'u sadece görsellik için kullanın.

## 7. Quickmap (LROC) ⭐⭐⭐ ✅ (resmi kaynak)

- **Ne:** Arizona State Üniversitesi'nin LRO görüntüleri, topografya ve diğer katmanları içeren interaktif Ay haritası.
- **Linkler:** https://quickmap.lroc.asu.edu/ · https://quickmap.lroc.im-ldi.com/ (ekip notlarındaki güncel alan adı)
- **Projede rolü:** Geliştirme sırasında **iniş yerlerini keşfetmek, koordinat almak, sonuçlarımızı gözle kontrol etmek**. Uygulamaya gömülecek bir API olarak değil, ekip aracı olarak.

## 8. İniş yeri listeleri ⭐⭐⭐

Görev, "Apollo, CLPS hedefleri, diğer ticari görevler ve diğer ülkelerin iniş yerleri" listesini öneriyor.

| Kaynak | Ne verir | Link |
|---|---|---|
| NASA CLPS Deliveries ✅ | Tüm CLPS görevleri ve hedefleri (Malapert A, Mare Crisium, Mons Mouton, Reiner Gamma, Schrödinger, Gruithuisen Domes, uzak yüz…) | https://science.nasa.gov/lunar-science/clps-deliveries/ |
| Artemis III aday bölgeleri 🔎 | 2024'te açıklanan 9 güney kutbu bölgesi: Peak near Cabeus B, Haworth, Malapert Massif, Mons Mouton Plateau, Mons Mouton, Nobile Rim 1, Nobile Rim 2, de Gerlache Rim 2, Slater Plain | https://www.upi.com/Science_News/2024/10/28/nasa-artemis-moon-landing-regions/5971730152372/ (resmi NASA sayfasından koordinat alınmalı) |
| USGS Gazetteer of Planetary Nomenclature ✅ | IAU onaylı tüm Ay krater/dağ adları + koordinatları (CSV/KML indirilebilir) | https://planetarynames.wr.usgs.gov/ |
| LROC IM-2 iniş bölgesi 🔎 | Mons Mouton görüntü ve konum | https://www.lroc.asu.edu/images/1401 |

**Başlangıç listesi (koordinatlar yaklaşık — kullanmadan önce Quickmap'te doğrulayın):**

| Yer | Enlem | Boylam (Doğu +) | Kaynak / not |
|---|---|---|---|
| Apollo 11 – Huzur Denizi | 0.67° K | 23.47° D | Genel bilgi |
| Apollo 15 – Hadley Rille | 26.13° K | 3.63° D | Genel bilgi |
| Apollo 17 – Taurus-Littrow | 20.19° K | 30.77° D | Genel bilgi |
| IM-1 Odysseus – Malapert A | 80.13° G | 1.44° D | 🔎 Arama sonucu |
| IM-2 Athena – Mons Mouton | 84.78° G | 29.13° D | 🔎 Arama sonucu (hedef nokta) |
| Blue Ghost 1 – Mare Crisium | 18.56° K | 61.81° D | 🔎 İkincil kaynak |
| Chandrayaan-3 (Hindistan) | ~69.4° G | ~32.3° D | Genel bilgi |
| Chang'e 4 (Çin) – von Kármán | ~45.4° G | ~177.6° D | Genel bilgi (uzak yüz → Dünya hiç görünmez, iyi bir "eğitici" örnek) |
| SLIM (Japonya) | ~13.3° G | ~25.2° D | Genel bilgi |

## 9. NASA SVS CGI Moon Kit ⭐⭐⭐ ✅

- **Ne:** 3D yazılımlar için hazırlanmış tüm-Ay **renk haritası** (2025 sürümü, kutuplarda daha detaylı) ve **yükseklik haritası**. EXR ve çeşitli boyutlarda 16-bit TIFF.
- **Link:** https://svs.gsfc.nasa.gov/4720/
- **Projede rolü:** Three.js ile kendi Ay küremizi yaparsak doku ve kabartma. (Cesium kullanırsak gerek kalmaz.)

## 10. Yüksek çözünürlüklü yakın çekim DEM'leri ⭐⭐

Tek bir iniş yerine çok yakından bakmak için. Dosyalar büyük — sadece demo için 1–2 yer seçilirse.

| Kaynak | Ne | Link |
|---|---|---|
| PGDA 78 🔎 | Güney kutbu seçili yerler için yüksek çözünürlüklü LOLA topografyası | https://pgda.gsfc.nasa.gov/products/78 |
| PGDA 81 🔎 | Güney kutbu LOLA DEM mozaiği | https://pgda.gsfc.nasa.gov/products/81 |
| PGDA 104 🔎 | Seçili güney kutbu bölgeleri için gölgelemeden şekil (shape-from-shading) ile iyileştirilmiş topografya | https://pgda.gsfc.nasa.gov/products/104 |
| LROC NAC DTM 🔎 | Apollo yerleri için metre altı ortofoto + DTM (yüzlerce MB – GB'lar) | ör. https://data.lroc.im-ldi.com/lroc/view_rdr_product/NAC_DTM_APOLLO11_M150368601_50CM |

## 10b. Yarışma referansları

- [HackAthlone görev kopyası ve kaynakları](https://hackathlone.com/challenges/lunar-landing-site-navigator) (ekip notlarından)
- [NASA Space Apps GO Team açıklaması](https://github.com/nasa/spaceapps/discussions/1827) — Global Awards kaynak kuralı ve başlangıç saati

## 11. CSA Moon Exploration ⭐ ✅ (resmi kaynak)

- **Ne:** Kanada Uzay Ajansı'nın Ay keşfi portalı; Artemis'e katkıları, geçmiş/gelecek görevler.
- **Link:** https://www.asc-csa.gc.ca/eng/astronomy/moon-exploration/
- **Projede rolü:** Teknik veri değil; sunumda bağlam ve "ortak ajans kaynağı kullandık" maddesi.

---

## Önerilen veri akışı (hackathon için en güvenli yol)

```
                    ÖNCEDEN (Python, hackathon başında)                       UYGULAMA (Web)
 ┌──────────────┐   ┌─────────────────────────┐   ┌──────────────────┐   ┌───────────────────────┐
 │ Yer listesi  │──▶│ Horizons / Skyfield      │──▶│                  │   │ Moon Trek / Cesium     │
 │ (8. kaynak)  │   │ Güneş & Dünya AZ/EL      │   │  sites.json      │──▶│ harita + 3D görünüm    │
 └──────────────┘   │ (saatlik, birkaç ay)     │   │  (yer başına:    │   │ Zaman kaydırıcı        │
                    └─────────────────────────┘   │   ufuk maskesi + │   │ Güç / iletişim grafiği │
 ┌──────────────┐   ┌─────────────────────────┐   │   zaman serisi)  │   │ Yer karşılaştırma      │
 │ LOLA DEM     │──▶│ Ufuk maskesi             │──▶│                  │   └───────────────────────┘
 │ (2. kaynak)  │   │ (360 yön × ufuk açısı)   │   └──────────────────┘
 └──────────────┘   └─────────────────────────┘          ▲
                                                         │ doğrulama / ısı haritası
                                             PGDA Illumination (3. kaynak)
```

### Ufuk maskesi nasıl hesaplanır (özet)
1. İniş noktası etrafında her azimut yönü için (ör. 1° adımla 360 yön) DEM üzerinde dışarı doğru ilerle (ör. 50–150 km'ye kadar).
2. Her noktada yükseklik açısını hesapla: `açı = atan((h − h0) / d) − d / (2R)` (R = 1737,4 km; ikinci terim Ay'ın eğriliği).
3. O yöndeki **en büyük açı** = o yönün ufku.
4. Sonra her saat için: `Güneş görünür = Güneş_EL > ufuk(Güneş_AZ)`, `Dünya görünür = Dünya_EL > ufuk(Dünya_AZ)`.

### Normalize veri şeması (ekip notlarından)

Benzersiz satır anahtarı: `(site_id, sampled_at_utc)`. Güneş ve Dünya tabloları UTC zaman damgasına göre eşleştirilir.

| Alan | Tip / birim | Açıklama |
|---|---|---|
| `site_id` | Metin | Adayın sabit kimliği |
| `longitude_deg_east` | Derece | Doğu pozitif, [0, 360) |
| `latitude_deg` | Derece | Güney negatif, [−90, 90] |
| `altitude_km` | km | Referans yüzeyine göre gözlemci yüksekliği |
| `sampled_at_utc` | ISO 8601 UTC | ör. `2026-11-14T00:00:00Z` |
| `sun_azimuth_deg`, `earth_azimuth_deg` | Derece veya null | [0, 360) |
| `sun_elevation_deg`, `earth_elevation_deg` | Derece veya null | [−90, 90]; negatifler korunur |
| `source` | Metin | NASA/JPL Horizons |
| `sun_request_id`, `earth_request_id` | Metin | Ham sorgu kaydı |

### Görünürlük pencereleri (ekip notlarından)

1. **Açıklık (clearance):** `açıklık = hedef_yüksekliği − ufuk_açısı(hedef_azimutu)`. Pozitif → görünür, negatif → ufuk altında, 0 → sınır. Düz ufukta `ufuk_açısı = 0`.
2. **Arazi ufku:** Ufuk profilinde azimutun **359°–0° geçişinde dairesel enterpolasyon** yapın. DEM kapsaması yetersizse sonucu "bilinmiyor" işaretleyin.
3. **Geçiş anı:** Açıklık işaret değiştirirse doğrusal enterpolasyonla tahmin edin; sınıra yakın aralıkları **5 dk** (gerekirse 1 dk) adımla yeniden sorgulayın. İki saatlik örnek arasında birden çok geçiş olabilir.
4. **Pencere kaydı:** `site_id`, `target`, `start_utc`, `end_utc`, `duration_s`, `horizon_model`, `refinement_step_s`, `quality_status`. Analiz sınırına dayanan pencereler "kırpılmış" işaretlenir; eksik veri üzerinden pencere birleştirilmez.
5. **Aday karşılaştırması:** toplam görünür süre, görünürlük yüzdesi, en uzun kesintisiz pencere, en uzun kesinti, bilinmeyen süre.

### Metrikler
- **Güneş yükseklik açısı** ve **güneşli saat oranı** (%).
- **Güç tahmini:** Kutupta Güneş çok alçak (~0–2°) olduğu için yatay panel neredeyse işe yaramaz; **dikey panel** varsayımı ile `güç ∝ cos(Güneş_EL)` (Güneş görünürse), yatay panel için `∝ sin(Güneş_EL)`. İkisini karşılaştırmak güçlü bir eğitici nokta.
- **Gün batımına / Dünya batımına kalan süre** (görevde özellikle vurgulanıyor).
- **İletişim penceresi:** Dünya'nın ufkun üstünde olduğu kesintisiz aralıklar.
- **Kısmi Güneş diski:** `QUANTITIES='4,13'` ile açısal çapı alın (yay saniyesi → derece, ikiye bölün = yarıçap). `merkez + yarıçap > ufuk` → kısmen görünür; `merkez − yarıçap > ufuk` → tamamen görünür.

### Hesaplama sınırları (sunumda dürüstçe belirtilmeli)
- **Güneş enerjisi:** Görünürlük yalnızca enerjiye *erişim potansiyelidir*. Gerçek üretim panel yönelimi, geliş açısı, panel verimi ve tutulmalara bağlıdır.
- **Dünya iletişimi:** `COMMAND=399` Dünya *merkezidir*. Gerçek bağlantı için ayrıca bir yer istasyonunun (DSN: Goldstone, Madrid, Canberra) Ay'ı görmesi, minimum yükseklik açısı, anten yönelimi ve bağlantı bütçesi gerekir.
- **Arazi doğruluğu:** DEM çözünürlüğü, yükseklik belirsizliği, kapsama ve panel/anten yüksekliği ufka yakın geçişleri etkiler. DEM ile Horizons'un koordinat çerçevesi ve yükseklik referansı eşleştirilmelidir. API'nin verdiği ondalık basamak sayısı pencerenin doğruluğu değildir.

---

## Yardımcı kütüphaneler

| Araç | Ne için |
|---|---|
| `requests` / `astroquery.jplhorizons` (Python) | Horizons sorguları |
| `skyfield`, `spiceypy` (Python) | Çevrimdışı konum hesabı |
| `rasterio` / GDAL (Python) | LOLA GeoTIFF okuma, kırpma |
| `numpy` | Ufuk maskesi hesabı |
| CesiumJS veya Three.js | 3D görünüm |
| Leaflet / OpenLayers | 2D WMTS harita |
| Chart.js / D3 | Zaman serisi, güç/iletişim grafikleri |
| `geotiff.js` | Gerekirse GeoTIFF'i doğrudan tarayıcıda okumak |

---

## Açık kontroller (ilk saatlerde yapılmalı)

- [x] Horizons örnek sorgusunu çalıştır, `coord@301` + `SITE_COORD` sözdiziminin doğru olduğunu teyit et. (🧪 ekip tarafından yapıldı)
- [ ] Güneş/Dünya konumu için tek motor seç: Horizons (resmi kaynak) mı, SPICE/Skyfield mı? (user-scenarios.md SPICE varsayıyor)
- [ ] PGDA 90'dan 60–120 m/piksel DEM'i indir, bir yer için ufuk maskesi çıkar.
- [ ] Hesaplanan güneşlenme oranını PGDA 69 haritasıyla karşılaştır.
- [ ] Artemis III 9 bölgesinin koordinatlarını NASA'nın resmi sayfasından al.
- [ ] 🔎 işaretli linkleri aç ve doğrula.
- [ ] Moon Trek'te kutup projeksiyonlu bir katman seç ve `WMTSCapabilities.xml`'ini al.
