# Radio ACCU Clipper 2.2

Desktopapp voor **macOS en Windows** (PySide6 + FFmpeg) om uit lange DJ-sets clips voor Instagram (1080 × 1350) te maken.
Alles draait op je eigen computer: geen cloud, abonnement of watermerk. Bronvideo's worden alleen gelezen.

## Delen met vrienden: echte installers voor Windows en Mac (nieuw in 2.2)

Je vrienden hoeven niets te weten van Python: ze krijgen een gewone installer met alles erin (FFmpeg, yt-dlp, Deno).

| Bestand | Voor |
|---|---|
| `Radio-ACCU-Clipper-<versie>-Windows-Setup.exe` | Windows 10/11 (64-bit) — installeren zonder beheerdersrechten, met Startmenu-snelkoppeling en verwijderfunctie |
| `Radio-ACCU-Clipper-<versie>-Windows-portable.zip` | Windows zonder installeren: uitpakken en `Radio ACCU Clipper.exe` starten |
| `Radio-ACCU-Clipper-<versie>-Mac-AppleSilicon.dmg` | Macs met M1/M2/M3/M4, macOS 15 Sequoia of nieuwer |
| `Radio-ACCU-Clipper-<versie>-Mac-Intel.dmg` | Intel-Macs, macOS 15 Sequoia of nieuwer |

**Waarom via GitHub?** Een Windows-programma kan alleen op Windows gebouwd worden en een Mac-app alleen op een Mac.
GitHub stelt gratis echte Windows- en Mac-computers beschikbaar die de installers automatisch bouwen, testen en
klaarzetten. Je hoeft dat maar één keer in te stellen.

### Eenmalig instellen (± 15 minuten)

1. Maak een gratis account op [github.com](https://github.com).
2. Klik rechtsboven op **+** → **New repository**. Naam: `radio-accu-clipper`. Kies **Public**: dan is het bouwen
   onbeperkt gratis. (Private kan ook, maar dan geldt een beperkt gratis maandtegoed waarvan Mac-minuten zwaarder tellen.)
   Klik **Create repository**.
3. Klik op **uploading an existing file**. Open in Finder de uitgepakte map `radio_accu_clipper`, druk op
   **⌘ ⇧ . (Command-Shift-punt)** zodat ook de verborgen map `.github` zichtbaar wordt, selecteer **alles** in de map
   (⌘A) en sleep het naar de webpagina. Klik **Commit changes**.
   *Controle:* in je repository moet een map `.github` staan. Zonder die map gebeurt er niets.
4. Open het tabblad **Actions**. Klik zo nodig op de groene knop om Actions in te schakelen.

### Een versie maken voor je vrienden

1. Ga in je repository naar **Releases** (rechts op de hoofdpagina) → **Draft a new release**.
2. Bij *Choose a tag* typ je bijv. `v2.2.0` en kies **Create new tag**. Titel: `Radio ACCU Clipper 2.2.0`.
3. Klik **Publish release**. Het bouwen start automatisch (tabblad Actions; reken op 30–60 minuten).
4. Daarna staan de vier bestanden onder die release. Stuur je vrienden de link naar de release-pagina; daar
   staat ook welk bestand ze nodig hebben en hoe ze het de eerste keer openen.

Alleen even proberen zonder release? Actions → **Installers bouwen** → **Run workflow**. De bestanden staan dan
onderaan de pagina van die run (bij *Artifacts*).

Is een stap rood (mislukt)? Klik erop, kopieer de foutmelding en stuur die naar mij.
Elke run toont ook een samenvatting met de resultaten van de tests en van de rooktest met echte Qt.

### Eerste keer openen bij je vrienden (niet te vermijden zonder betaald certificaat)

- **Windows:** "Windows heeft uw pc beschermd" → **Meer informatie** → **Toch uitvoeren**. Sommige virusscanners
  zijn extra wantrouwig bij nieuwe, niet-ondertekende programma's; dat is een bekende vals alarm bij dit soort apps.
- **Mac:** sleep de app naar Programma's en open hem. Meldt macOS "Apple kon niet verifiëren…": klik **Gereed**, open
  **Systeeminstellingen → Privacy en beveiliging** en klik onderaan op **Open toch**. Daarna opent hij gewoon.
  (Dit staat ook in "Eerst lezen.txt" in het installatievenster.)

Wil je die meldingen kwijt? Dat kan alleen met betaalde ondertekening: een Apple Developer-account (99 dollar per jaar)
voor de Mac en een code-signing-certificaat voor Windows. De bouwopdracht ondersteunt Apple-ondertekening en
notarisatie al: zet daarvoor in je repository onder Settings → Secrets → Actions de waarden `MAC_CERT_P12_BASE64`,
`MAC_CERT_PASSWORD`, `MAC_SIGN_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID` en `APPLE_APP_PASSWORD`. Dit deel heb ik niet
kunnen testen.

## Zelf installeren op je eigen Mac uit de broncode

1. Pak de ZIP uit.
2. Dubbelklik **`Installeer.command`**. (Eerste keer: Control-klik → *Open*. Lukt dubbelklikken niet, ga dan eenmalig
   in Terminal naar de map en voer `chmod +x Installeer.command` uit.)
3. Het script controleert Python 3.10+ en FFmpeg (installeert FFmpeg via Homebrew als dat aanwezig is), installeert
   voor links **yt-dlp** (in `~/Library/Application Support/Radio ACCU Clipper/tools`) en **Deno** (via Homebrew), bouwt de
   app en zet **Radio ACCU Clipper.app** in `~/Applications`. Sleep de app naar het Dock; daarna start je hem gewoon met een klik.
4. Eerste keer openen: Control-klik op de app → *Open* (de app is lokaal ondertekend, niet door Apple geverifieerd).

Alternatief zonder bouwen: `Start_zonder_installeren.command` start de app direct uit de broncode.
Wil je FFmpeg in de app inbouwen? Zet vóór het bouwen `ffmpeg` en `ffprobe` (arm64) in de map `vendor/`.

## Gebruik

1. Sleep video's in het venster (of *Video's toevoegen…*). De audio wordt één keer geanalyseerd voor de waveform
   (daarna gecachet; een uur audio kost enkele seconden tot een halve minuut).
2. Tab **Clips**: kies modus, duur, aantal → *Zoek suggesties*; of zet de afspeelkop en kies *Handmatig*.
   Zwakke suggesties staan oranje en standaard uitgevinkt. Alleen aangevinkte clips worden geëxporteerd.
3. Pas start/einde aan door de gele handvatten op de tijdlijn te slepen, of typ een tijd (`90`, `1:30`, `0:01:30`).
   Sneltoetsen: Spatie, ←/→ (1 frame; Shift = 1 s), I / O.
4. Tab **Beeld**: beeldvullend croppen of volledig beeld met zwarte randen; sleep het beeld in het voorbeeld om te croppen.
   Tab **Kwaliteit / Audio / Branding**: zie de uitleg in de app. Tab **Export**: map kiezen en exporteren.
5. Bestand → *Project opslaan* bewaart alle sets, clips en instellingen. De vorige sessie wordt bij afsluiten bewaard.

## Links (nieuw in 2.1)

- Klik **Link toevoegen** (of ⌘L), of sleep een link uit je browser in het venster. Een gekopieerde link wordt automatisch ingevuld.
  Meerdere links tegelijk kan: één per regel.
- Werkt met YouTube, SoundCloud, Mixcloud, Vimeo, Twitch-VOD's, Dropbox, directe links naar video-/audiobestanden en de
  vele andere sites die [yt-dlp](https://github.com/yt-dlp/yt-dlp) ondersteunt. De download komt in `~/Movies/Radio ACCU Downloads`
  (aanpasbaar) en verschijnt daarna als gewone set.
- **Kwaliteit**: beste (tot 4K, .mkv), tot 1440p, tot 1080p, *compatibel* (H.264/AAC .mp4) of *alleen audio*.
  Voor een 1080 × 1350-crop uit een liggende video geeft 1440p of hoger scherpere clips dan 1080p.
- **Alleen een fragment**: bijv. 1:00:00–1:30:00 van een lange set; scheelt veel downloadtijd en schijfruimte.
- **Afspeellijsten**: de app vraagt of je alle items wilt downloaden.
- **Tracklist en hoofdstukken**: vink aan om de tijden uit de YouTube-beschrijving (of de hoofdstukken) direct als clips te
  krijgen, of gebruik later *Clips uit tracklist of hoofdstukken…* in de tab Clips. Je kunt daar ook zelf een tracklist plakken
  en instellen hoeveel seconden na het trackbegin de clip start.
- **Cookies uit browser**: alleen nodig als een site om inloggen vraagt (bijv. YouTube: "bevestig dat je geen bot bent").
  Voor Safari heeft de app *Volledige schijftoegang* nodig; Chrome of Firefox werkt zonder.
- Werkt een site ineens niet meer: Help → *yt-dlp bijwerken*. Sites veranderen regelmatig en yt-dlp volgt die wijzigingen.
- Download alleen materiaal waarvoor je de rechten of toestemming hebt.

## Andere nieuwe opties in 2.1

- **Audiobestanden en audio-only links** (mp3, wav, m4a, SoundCloud, Mixcloud): de clip krijgt een zwart 1080 × 1350-beeld,
  standaard met een golfvorm van de muziek (kleur instelbaar, tab Beeld). Logo en DJ-naam komen er gewoon overheen.
- **Omslagfoto** (JPG, 1080 × 1350) uit het midden van elke clip, voor Instagram (tab Export).
- **Instagram-bijschrift** als tekstbestand naast elke clip, met een eigen sjabloon en invulvelden zoals {title}, {dj}, {label}
  (tracknaam) en {start} (tab Export).
- Hoofdstukken in videobestanden worden ook bij lokale bestanden gelezen.

## Wat is er geïmplementeerd

Volledig (en voor zover genoemd getest, zie onder): eigen venster met Dock-icoon · native bestandskiezers + drag-and-drop ·
meerdere sets in een wachtrij · achtergrondverwerking met voortgang en annuleren · tijdelijke bestanden worden opgeruimd ·
alle uitvoer exact 1080×1350 · fill/fit, zoom, crop slepen · rotatie en pixelverhouding · H.264 MP4, CRF/preset/profielen,
VideoToolbox-optie, lossless · FPS bron/24/25/30/50/60 · HDR-detectie met tone-mapping naar SDR (of HEVC 10-bit behouden) ·
helderheid/contrast/verzadiging · video- en audiofades · AAC-bitrate, gain, mute, normalisatie (loudness of piek) met limiter ·
logo (PNG met transparantie) en DJ-naam, sleepbaar, zelfde render in preview en export · heuristische highlights
(balanced/drops/transitions/high energy) zonder overlap, met eerlijke melding bij weinig of zwakke momenten ·
handmatige clips, dupliceren, verwijderen, instellingen per clip en toepassen op alles · unieke bestandsnamen (nooit overschrijven) ·
schijfruimtecontrole · overzicht (`.txt` + `.json`) bij elke export · één mislukte clip stopt de wachtrij niet ·
links downloaden met voortgang, annuleren en opruimen · afspeellijsten · fragmenten · tracklist/hoofdstukken als clips ·
audio-only bronnen met golfvorm · omslagfoto · bijschrift.

**Bewust niet / beperkt:**
- Het live voorbeeld speelt het bronbestand zelf af (geen proxy met lagere resolutie): zeer zware 4K/HEVC-bestanden kunnen
  haperen. De export is daar niet van afhankelijk.
- Kleur en fades zijn niet in het live voorbeeld zichtbaar; de knop *Toon exact exportframe* laat via FFmpeg het echte frame zien (kleur wel, fades niet).
- Framegewijs navigeren heeft millisecondenprecisie (afhankelijk van de speler), niet altijd exact één frame.
- Kleurconversie: matrix wordt naar BT.709 omgezet; afwijkende primaries (bijv. oude SD-bronnen) worden niet omgerekend.
  Dolby Vision zonder HDR10-basislaag wordt gemeld maar niet speciaal behandeld.
- Highlightdetectie is signaalanalyse (energie, bas, spectrale verandering), geen AI en geen muziekbegrip.
  Luister suggesties altijd na. De ingebouwde AAC-encoder haalt 320 kbps niet altijd bij eenvoudig materiaal;
  op de Mac kun je *Apple AudioToolbox AAC* kiezen (tab Audio) als die beschikbaar is.
- Geen undo-functie.
- Livestreams die nog bezig zijn kunnen niet worden gedownload. Bij een fragment-download wordt op keyframes geknipt
  (enkele seconden speling) en kloppen tracklist-tijden niet meer.
- Het live voorbeeld van een audio-only set toont zwart; de golfvorm zie je via *Toon exact exportframe* en in de export.

## Wat is getest (Linux-omgeving, FFmpeg echt uitgevoerd)

`bash tests/run_all.sh` — 65 tests + rooktest, alle geslaagd:
- Exports op test-bronnen (liggend, rotatiemetadata, anamorf 4:3-pixels, 59,94 fps, HDR PQ, zonder audio): altijd **1080×1350**, yuv420p, h264.
- Crop-positie (links/midden/rechts, zoom) en fit-modus (zwarte randen, juiste hoogte, ook bij anamorfe bron) gecontroleerd op pixelwaarden.
- FPS: bron (ook 59,94), 24, 25, 30, 50, 60 gemeten. Duur binnen 0,1 s.
- Audio-video-sync: beeld- én geluidsomslag in de testbron komen beide op de verwachte 2,0 s uit. Fades, mute, gain zonder clipping, piek- en loudness-normalisatie gemeten.
- Logo (positie, transparantie) en tekst-overlay op pixelniveau gecontroleerd. HDR → SDR (BT.709-tags) en HDR behouden (HEVC 10-bit, PQ).
- Bestandsnamen met spaties, accenten en tekens als `#()[]–`; nooit overschrijven; annuleren laat geen bestanden achter;
  mislukte clip stopt wachtrij niet; bron blijft onaangetast; schijfruimtecontrole.
- Analyse: synthetische set met bekende drops (90 s, 180 s) en breakdown (150 s) wordt correct gevonden; geen overlap; een uur audio ≈ 7 s en ≈ 150 MB RAM.
- Links met een **nagebootste yt-dlp** (geen internet in mijn omgeving): gegevens en hoofdstukken ophalen, afspeellijst,
  videodownload met voortgang in twee delen, audio-only, fragment, opnieuw gebruiken van een bestaande download,
  annuleren (geen restbestanden), fout halverwege, botcontrole → cookie-advies, schijfruimte, URL na `--` (veilig).
- Tracklists in diverse notaties; audio-only export (1080×1350, golfvorm op de juiste plek, sync); omslagfoto; bijschrift.
- Rooktest van de interfacelogica (toevoegen, analyseren, clips bewerken, exporteren, project opslaan/laden, links →
  download → clips uit tracklist → export) met een **nep-Qt** die nu ook controleert dat elk veld alleen zijn eigen signalen heeft.

## Wat NIET getest is en op jouw Mac gecontroleerd moet worden

Ik had geen macOS, geen PySide6 en geen Apple-hardware. Daarom is het volgende **niet** daadwerkelijk uitgevoerd:
1. **De Qt-interface zelf**: opmaak, knoppen, tijdlijn tekenen en slepen, drag-and-drop, sneltoetsen. De code is geschreven volgens de PySide6-API en syntactisch gecontroleerd, maar nooit met echt Qt gedraaid. Reken op kleine bugs; meld ze gerust.
2. **Videoweergave in het voorbeeld** (QMediaPlayer): afspelen met geluid, springen/scrubben, en of rotatiemetadata en pixelverhouding in het voorbeeld goed staan.
3. **De build**: `Installeer.command` / PyInstaller / ondertekening / Gatekeeper, en of FFmpeg gevonden wordt (Homebrew-pad `/opt/homebrew/bin` wordt expliciet doorzocht).
4. **VideoToolbox**-export en de **AudioToolbox**-AAC-encoder (alleen het commando is opgebouwd).
5. **Echte downloads**: yt-dlp heb ik niet tegen echte sites kunnen draaien. Het formaat van de voortgang en de
   opties volgen de documentatie van yt-dlp, maar test een YouTube-link, een SoundCloud-link en een fragment-download.
   Ook de installatie van yt-dlp en Deno door `Installeer.command` is nog niet op een Mac gedraaid.
6. Snelheid en geheugen op jouw Mac bij multi-GB bronnen (de streaming-opzet is gemeten, maar niet op Apple Silicon).
7. Controleer een paar echte exports van je eigen sets: kleur t.o.v. de bron, crop, branding en geluid, bij voorkeur ook één HDR-bron van je iPhone of camera.

Mocht de app niet starten: draai `Start_zonder_installeren.command` en stuur de melding die in Terminal verschijnt.

## Wat is getest voor 2.2 (installers)

Op de echte bouwmachines van GitHub (Windows Server, macOS 15 Apple Silicon en macOS 15 Intel) draait bij elke bouw:
- een controle dat de meegeleverde FFmpeg werkt en x264, x265, zscale (HDR) e.d. bevat — inclusief een echte
  HDR-naar-SDR-codering; de bouw stopt als iets ontbreekt;
- alle 75 tests (export, crop, FPS, audio/sync, HDR, branding, analyse, links, tracklists, platformgedrag);
- een rooktest met **echte Qt**: venster, alle 40 instelvelden, inlezen, analyse, invoervelden, DJ-naam renderen,
  export naar 1080×1350, link + tracklist, project opslaan/openen, afsluiten. Resultaat: 14/14 op alle drie.

Wat een bouwmachine niet kan nabootsen en je dus zelf even moet proberen: de installer echt installeren en openen,
video afspelen met beeld en geluid, slepen in de tijdlijn en het voorbeeld, drag-and-drop vanuit Finder/Verkenner,
een echte YouTube-download, en VideoToolbox-export op een echte Mac.

## Structuur

`accu_clipper/` — `exporter.py` (filtergraaf, kwaliteit, audio, export) · `layout.py` (crop-geometrie, gedeeld door preview en export) ·
`features.py` + `highlights.py` (analyse) · `downloader.py` (links via yt-dlp) · `tracklist.py` · `ffmpeg_tools.py` (probe) ·
`model.py` (projecten) · `platform_paths.py` (verschillen Mac/Windows) · `gui/` (PySide6-interface en dialogen) · `tests/` ·
`packaging/` (`fetch_tools.py`, `verify_tools.py`, `build.py`, Inno Setup-script, Mac-bestanden) · `.github/workflows/build.yml`.
