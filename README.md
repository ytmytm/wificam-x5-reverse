# TXW817 / WiFiCam X5 — lokalny odbiornik, RTSP i Home Assistant

Projekt odbiera obraz i audio z taniej kamery WiFi sprzedawanej jako **X5**,
rozpoznawanej przez firmware jako **TXW817**. Media po zestawieniu sesji płyną
bezpośrednio po LAN do serwera Home Assistant, a wynik jest publikowany jako
RTSP. Sterowanie LED/IR jest wystawiane przez MQTT Discovery.

To niezależny projekt reverse-engineeringu. Używaj go tylko z własną kamerą i
własną siecią.

## 1. Co jest potrzebne

Docelowo potrzebny jest stale działający serwer Home Assistant z Ubuntu/ARMv7,
FFmpeg i dostępem do LAN. Testowana instalacja to HA 2023.10.3 na Pythonie
3.11. Projekt nie wymaga Dockera, Node.js ani npm.

Potrzebne są również:

- kamera X5/TXW817;
- telefon z aplikacją **[Z-IOT CAM z Google Play](https://play.google.com/store/apps/details?id=com.flu.flutter_wifi_camera)**;
- router z DHCP i dostępem kamery do LAN;
- jednorazowy dostęp aplikacji do vendorowej chmury podczas uzyskiwania tokenu;
- broker MQTT, domyślnie `iotlocal.lan:1883`.

## 2. Pierwsza konfiguracja kamery

Na początku aplikacja vendora jest praktycznie niezbędna. Służy do:

1. zasilenia i zresetowania kamery, jeśli jest to wymagane;
2. połączenia telefonu z hotspotem konfiguracyjnym kamery;
3. przekazania kamerze danych domowej sieci WiFi;
4. potwierdzenia, że kamera pojawia się w routerze i działa w podglądzie;
5. odczytania/wynegocjowania UID oraz uzyskania ruchu, z którego można
   wyciągnąć token API.

Według instrukcji dla tej rodziny urządzeń hotspot może mieć nazwę zaczynającą
się od `ZIOT`. Po zakończeniu konfiguracji kamera powinna dostać adres DHCP,
np. `192.168.2.158`. Aplikacja jest potrzebna do początkowego provisioningu;
nasz serwer nie zastępuje tej procedury konfiguracji WiFi.

## 3. UID, adres IP i token

Do automatycznego startu potrzebne są trzy wartości:

```dotenv
X5_UID=276642521291
X5_CAMERA_IP=192.168.2.158
X5_API_TOKEN=local-private-token
```

### UID

UID to numeryczny identyfikator kamery używany przez API. Nie jest nim adres IP
ani MAC. Najpewniejsze źródło to przechwycenie sesji live aplikacji i wyszukanie
`uid=` albo pola JSON `uid`. UID pojawia się także w body komendy LED/IR.

Można przeszukać prywatny capture lokalnie:

```sh
strings /path/to/x5-full.pcap | rg -o \
  '[?&]uid=[0-9]+|"uid"[[:space:]]*:[[:space:]]*"?[0-9]+'
```

### Token

Token bearer wyciąga się jednorazowo z capture ruchu aplikacji. Na routerze,
podczas gdy telefon otwiera podgląd kamery, uruchom prywatny capture:

```sh
tcpdump -i br-lan -s 0 -U -w /tmp/x5-full.pcap \
  'host <camera-ip> or host <phone-ip>'
```

W aplikacji zaloguj się, otwórz kamerę i poczekaj na widoczny obraz. Zatrzymaj
`tcpdump`, skopiuj capture na zaufany komputer i uruchom:

```sh
./tools/extract_token.sh /path/to/x5-full.pcap
```

Skrypt wypisze gotowe linie `X5_API_TOKEN`, `X5_UID` i — jeśli uda się wykryć
— `X5_CAMERA_IP`. Capture może zawierać token, dane konta i identyfikatory
urządzeń; nie publikuj go i nie commituj do Git. Jeśli aplikacja przejdzie na
HTTPS, pasywny capture może nie wystarczyć do odzyskania tokenu.

## 4. Konfiguracja prywatna

Skopiuj przykład i uzupełnij wartości:

```sh
cp config.env.example config.env
$EDITOR config.env
```

Minimalna konfiguracja:

```dotenv
X5_UID=276642521291
X5_CAMERA_IP=192.168.2.158
X5_API_TOKEN=replace-with-a-local-token
```

Najważniejsze opcje MQTT:

```dotenv
X5_MQTT_BROKER=iotlocal.lan
X5_MQTT_PORT=1883
X5_MQTT_PREFIX=wificam/x5
X5_MQTT_DISCOVERY_PREFIX=homeassistant
```

Nie wpisuj tokenu do plików śledzonych przez Git. `config.env` jest ignorowany,
a na serwerze konfiguracja trafia do `/etc/x5-viewer.env` z prawami `0600`.

## 5. Instalacja i uruchomienie

### Opcjonalny viewer diagnostyczny

Viewer służy tylko do testów i nie jest potrzebny na co dzień:

```sh
sudo ./install.sh config.env
systemctl status x5-viewer.service
curl http://127.0.0.1:5080/status
```

Podgląd: `http://<home-assistant-server-ip>:5080/`. Viewer udostępnia
`/stream.mjpg`, `/audio.pcm`, `/status` i przyciski LED/IR. Nie uruchamiaj go
jednocześnie z publisherem RTSP — oba procesy używają UDP `50001`.

### Normalny tryb RTSP

Jeśli środowisko nie było jeszcze instalowane, najpierw skopiuj prywatną
konfigurację do `/etc/x5-viewer.env`:

```sh
sudo ./install.sh config.env
```

Następnie uruchom normalny tryb RTSP:

```sh
sudo ./install-rtsp.sh
```

Instalator instaluje MediaMTX i `python3-paho-mqtt`, kopiuje odbiornik do
`/opt/x5-viewer`, wyłącza viewer diagnostyczny oraz włącza:

- `x5-mediamtx.service` — serwer RTSP na TCP `8554`;
- `x5-rtsp-publish.service` — negocjacja kamery, odbiór UDP i FFmpeg;
- konfigurację kolejności startu względem Home Assistant.

Stream: `rtsp://<home-assistant-server-ip>:8554/x5`.

Kontrola usług:

```sh
systemctl status x5-rtsp-publish x5-mediamtx home-assistant@homeassistant
journalctl -u x5-rtsp-publish.service -f
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/x5
```

## 6. Home Assistant

W Home Assistant używana jest jedna encja Generic Camera ze źródłem:

```text
rtsp://127.0.0.1:8554/x5
```

Dla kamery z UID `276642521291` encja ma nazwę:

```text
camera.wificam_x5_276642521291
```

Przy kolejnej kamerze należy utworzyć osobny publisher/config z innym UID,
adresem IP i portem UDP. UID jest używany także w encjach MQTT, więc urządzenia
nie kolidują w jednym HA.

### MQTT LED/IR

Publisher publikuje MQTT Discovery dla:

```text
switch.wificam_x5_276642521291_led
switch.wificam_x5_276642521291_ir
```

Topics są UID-scoped:

```text
wificam/x5/276642521291/led/set
wificam/x5/276642521291/ir/set
wificam/x5/276642521291/led/state
wificam/x5/276642521291/ir/state
wificam/x5/276642521291/availability
```

Stan jest ostatnim skutecznie żądanym stanem. Firmware nie udostępnia
wiarygodnego odczytu fizycznego stanu LED/IR, dlatego po ręcznej zmianie w
aplikacji HA może mieć nieaktualny stan. Brak brokera MQTT nie zatrzymuje RTSP.

MQTT służy tylko do sterowania i Discovery. Nie używamy MQTT Camera do obrazu,
bo ta integracja przesyła pojedyncze JPEG-i, a nie niskolatencyjny stream.

### Opóźnienie i HLS

Na testowanej instalacji HA 2023.10.3 używany jest stabilny HLS:

```yaml
stream:
  ll_hls: false
```

Daje to zwykle kilka sekund opóźnienia. LL-HLS na tej konkretnej wersji HA i
przeglądarce potrafił odtworzyć początkową playlistę, a następnie zatrzymać
żądanie kolejnych fragmentów, dlatego pozostaje wyłączony.

Napis „Brak aktywności” w karcie kamery jest stanem encji `generic camera`;
ta integracja nie raportuje `streaming/recording`, nawet gdy RTSP płynie. W
karcie można ukryć go tak:

```yaml
type: picture-entity
entity: camera.wificam_x5_276642521291
camera_view: live
show_state: false
```

## 7. Co jest lokalne, a co nadal zależy od chmury

Projekt nie zastępuje całkowicie vendorowej chmury. Przy starcie publisher używa
vendorowego HTTP API i tokenu do rejestracji endpointu UDP serwera Home
Assistant, żądania live event, odświeżania sesji oraz wysyłania komend LED/IR.

Po zestawieniu sesji właściwy obraz i audio płyną bezpośrednio z kamery do
serwera Home Assistant po UDP. Chmura nie przekazuje obrazu. To lokalny proxy
mediów z cloud-assisted setup, a nie rozwiązanie całkowicie cloud-free.

Capture pokazał, że firmware otwiera również TCP do vendorowego serwera na
porcie `8882`. Nie ustaliliśmy, czy hostname jest zaszyty w firmware, czy
pobierany z innej konfiguracji/DNS.

## 8. Stabilność i zachowanie offline

Publisher ponawia negocjację, gdy kamera znika z LAN, nie restartuje HA z
powodu braku kamery, powtarza ostatnią kompletną klatkę do 30 sekund podczas
krótkiej przerwy oraz odświeża zdrową sesję live co 20 sekund bez zmiany portu
UDP. Kolejki FFmpeg mają jeden pakiet, żeby nie odtwarzać starego obrazu przez
dziesiątki sekund lub minuty.

Jeżeli kamera jest wyłączona, publisher może działać bez klatek, a HA nadal
powinien się uruchomić. Po pojawieniu się kamery proces sam próbuje wznowić
sesję.

## 9. Diagnostyka

Sprawdzaj warstwy w tej kolejności:

1. `systemctl is-active x5-rtsp-publish x5-mediamtx home-assistant@homeassistant`
2. `journalctl -u x5-rtsp-publish.service -f`
3. `ffprobe -v error -rtsp_transport tcp rtsp://127.0.0.1:8554/x5`
4. `ss -lunp | grep 50001` oraz `ping <camera-ip>`

`negotiation succeeded` nie gwarantuje jeszcze klatek; szukaj
`first video frame assembled`. Brak ścieżki `x5` oznacza zwykle brak pierwszej
klatki w FFmpeg, a nie problem z autoryzacją HA. UDP `50001` powinien należeć
do dokładnie jednego odbiornika. Jeżeli `ffprobe` działa, a HA jest puste,
sprawdzaj Generic Camera, HLS i kartę dashboardu.

## 10. Specyfika tej instalacji Home Assistant

To obejście dotyczy wyłącznie naszego serwera HA 2023.10.3 na Pythonie 3.11,
ARMv7 i GCC 7.5 — nie jest ogólnym wymaganiem HA ani ARMv7. Ten serwer próbuje
instalować `numpy==1.26.0`, nie ma odpowiedniego wheel i nie może zbudować go
lokalnym GCC. Jest na nim NumPy 1.23.2, a unit HA używa
`--skip-pip-packages numpy`, żeby boot nie trwał wiele minut.

## 11. Pliki i development

| Plik/usługa | Rola |
| --- | --- |
| `x5_viewer.py` | wspólna negocjacja, odbiór UDP i opcjonalny viewer HTTP |
| `x5_rtsp_publish.py` | publisher RTSP, FFmpeg i lokalny control HTTP |
| `x5_mqtt.py` | MQTT Discovery, LED/IR i availability |
| `rtp_jpeg.py` | składanie fragmentów JPEG |
| `protocol.md` | opis zreverse-engineerowanego protokołu |
| `/etc/x5-viewer.env` | prywatny UID, IP, token i ustawienia |
| `x5-rtsp-publish.service` | stały odbiornik i publisher |
| `x5-mediamtx.service` | lokalny serwer RTSP |

```sh
./test.sh
python3 rtp_jpeg.py private-capture.pcap --out /tmp/x5-jpegs
```

`.gitignore` wyklucza capture, surowe media, logi, sekrety i cache Pythona.

## 12. Bezpieczeństwo

- Zaobserwowany endpoint API używa HTTP; traktuj token jak hasło.
- Nie publikuj `pcap`, `config.env`, `/etc/x5-viewer.env` ani historii shella.
- Viewer MJPEG nie ma uwierzytelniania; trzymaj go na zaufanym LAN.
- Obróć/revokuj token, jeśli capture lub konfiguracja opuści zaufane hosty.

## 13. Źródła

- [Z-IOT CAM w Google Play](https://play.google.com/store/apps/details?id=com.flu.flutter_wifi_camera) — aplikacja do początkowej konfiguracji;
- [DavidVentura/cam-reverse](https://github.com/DavidVentura/cam-reverse);
- [Elektroda, topic 4150688](https://www.elektroda.pl/rtvforum/topic4150688.html);
- [Elektroda, topic 4033757](https://www.elektroda.com/rtvforum/topic4033757.html);
- [Elektroda, topic 4117962](https://www.elektroda.com/rtvforum/topic4117962.html);
- [Elektroda, topic 4147085](https://www.elektroda.pl/rtvforum/topic4147085.html);
- [TXW817 manufacturer datasheet](https://taixin-semi.com/zh/docs/txw817/latest/TXW817%20%E6%95%B0%E6%8D%AE%E6%89%8B%E5%86%8C?navigation=1&series=txw81x);
- [RFC 2435 RTP/JPEG](https://www.rfc-editor.org/rfc/rfc2435.html);
- [FFmpeg RTP/JPEG depacketizer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/rtpdec_jpeg.c);
- [ACTi Streaming SDK guide](https://fen.home.pl/pub/webFTP/sterowniki/Acti/SDK/ACTi_SDK-10000_C_SDK_Programming_Guide-v1.2.33.pdf);
- [MediaMTX releases](https://github.com/bluenviron/mediamtx/releases);
- [Home Assistant Generic Camera](https://www.home-assistant.io/integrations/generic/).

Prywatne capture są podstawowym dowodem dla tego konkretnego modelu i nie są
częścią publicznego repozytorium.
