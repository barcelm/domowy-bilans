# 🧹 Domowy bilans — Shiny for Python na shinyapps.io

Suwak prac domowych dla dwóch domowników: każda praca ma wagę (punkty), suwak
przesuwa się w stronę osoby, która robi mniej. Strefa ⚠️ zagrożenia → komunikat
„podciągnij się”, strefa 🍽️ finalna → przegrany stawia kolację.

## Jak to działa

```
telefony domowników ──► shinyapps.io (app.py, darmowy plan)
                               │  Google Sheets API (konto serwisowe)
                               ▼
                    arkusz Google: zakładki log / kv / meta (dane + zdjęcia)
```

shinyapps.io kasuje pliki zapisane przez aplikację przy każdym uśpieniu, dlatego dane
trzymamy w arkuszu Google. Nic tu nie wymaga karty ani włączonych płatności.

Wszystkie polecenia poniżej można wykonać w **Cloud Shell** (ikona `>_` w console.cloud.google.com),
w folderze `domowy-bilans`.

## Krok 1. Konto serwisowe Google (dostęp aplikacji do arkusza)

```bash
PROJECT_ID=pracedomowe-510513          # Twój projekt (płatności niepotrzebne)
gcloud config set project $PROJECT_ID
gcloud services enable sheets.googleapis.com drive.googleapis.com

gcloud iam service-accounts create bilans-sheets --display-name="Domowy bilans"
gcloud iam service-accounts keys create service_account.json \
  --iam-account=bilans-sheets@$PROJECT_ID.iam.gserviceaccount.com

# adres e-mail konta serwisowego — przyda się w kroku 2
echo bilans-sheets@$PROJECT_ID.iam.gserviceaccount.com
```

Plik `service_account.json` musi leżeć obok `app.py`. To klucz dostępu — nie wrzucaj go
do publicznego repozytorium.

## Krok 2. Arkusz Google

1. Na sheets.google.com utwórz pusty arkusz (np. „Domowy bilans — dane”).
2. **Udostępnij** go adresowi konta serwisowego z kroku 1 z rolą **Edytor**
   (powiadomienie e-mail można odznaczyć).
3. Skopiuj adres arkusza z paska przeglądarki i zapisz go do pliku:
   ```bash
   echo "https://docs.google.com/spreadsheets/d/....../edit" > google_sheet_id.txt
   ```
   (może być cały link albo samo ID).

Zakładki `log`, `kv` i `meta` aplikacja utworzy sama przy pierwszym uruchomieniu.

## Krok 3. Konto shinyapps.io

1. Załóż darmowe konto na shinyapps.io i wybierz nazwę konta (np. `bartek`).
2. Wejdź w **Account → Tokens → Show** i skopiuj `token` oraz `secret`.

## Krok 4. Wdrożenie

```bash
pip install --user rsconnect-python
export PATH="$HOME/.local/bin:$PATH"

rsconnect add --account <NAZWA_KONTA> --name <NAZWA_KONTA> \
  --token <TOKEN> --secret <SECRET>

rsconnect deploy shiny . --name <NAZWA_KONTA> --title domowy-bilans
```

Po kilku minutach dostaniesz adres `https://<NAZWA_KONTA>.shinyapps.io/domowy-bilans/`.
Otwórz go na obu telefonach (warto „Dodać do ekranu początkowego”).

**Aktualizacja:** po zmianie kodu ponownie `rsconnect deploy shiny . --name <NAZWA_KONTA> --title domowy-bilans`.
Dane w arkuszu zostają.

## Krok 5 (zalecany). Oszczędzanie godzin

Darmowy plan shinyapps.io ma ograniczoną liczbę **aktywnych godzin w miesiącu** (godziny,
w których aplikacja jest uruchomiona). W panelu shinyapps.io: aplikacja → **Settings →
General → Instance Idle Timeout** ustaw na **5 minut** — aplikacja szybciej usypia,
gdy nikt z niej nie korzysta.

## Sprawdzenie

- Na stronie głównej aplikacji **nie powinno** być szarego baneru „Tryb lokalny”.
  Jeśli jest czerwony baner z błędem Google Sheets, zwykle oznacza to, że arkusz nie
  został udostępniony kontu serwisowemu albo w `google_sheet_id.txt` jest złe ID.
- Po dodaniu pierwszej pracy w arkuszu pojawi się zakładka `log` z wpisem.

## Kopia zapasowa

- Arkusz Google ma własną historię wersji (Plik → Historia wersji).
- W aplikacji: zakładka **Historia → Pobierz CSV** (oraz import CSV).

## Uruchomienie lokalne

```bash
pip install -r requirements.txt
shiny run --reload app.py      # http://127.0.0.1:8000
```

Bez `service_account.json` / `google_sheet_id.txt` dane zapisują się w folderze `./data`.
Z tymi plikami — do tego samego arkusza co wersja online.

## Stałe zdjęcia (opcjonalnie)

Możesz przed wdrożeniem wrzucić do folderu `photos/` pliki:
`p0.jpg`, `p1.jpg` (awatary), `danger.jpg`, `final.jpg`, `winner.jpg` (komunikaty).
Zdjęcia wgrane w aplikacji (zapisywane w arkuszu) mają pierwszeństwo.

## Konfiguracja w aplikacji (⚙️ Ustawienia)

- imiona domowników, progi stref, długość pamięci (domyślnie 31 dni), treść kary,
- lista prac i wag w formacie `Nazwa = punkty` (jedna na linię),
- „Rozlicz i wyzeruj bilans” — suwak wraca na środek, historia zostaje.

CSV do importu: kolumny `date` (RRRR-MM-DD lub DD.MM.RRRR), `chore` oraz
`person` (imię) lub `person_idx` (0/1); `weight` opcjonalnie.

## Dostęp

Kto zna adres aplikacji, ten może z niej korzystać (darmowy plan nie ma haseł).
Nie publikuj linku.

## Alternatywa: Google Cloud Run

`Dockerfile` i `deploy.sh` pozwalają wdrożyć aplikację na Cloud Run z danymi w buckecie
Cloud Storage (`PROJECT_ID=... ./deploy.sh`). Wymaga to aktywnego konta rozliczeniowego.
