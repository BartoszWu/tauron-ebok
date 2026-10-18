# TAURON eBOK

Eksperymentalna, niezależna integracja Home Assistant do Mój TAURON.

**0.3.0: miesięczna historia zużycia bez PDF, oddzielona od AMIplus i faktur.**
Logowanie i zapis konfiguracji potwierdzono na rzeczywistym koncie w 0.2.0.
Strukturę odpowiedzi historii i sposób grupowania potwierdzono w portalu;
każde wdrożenie wymaga sprawdzenia zgodności importu z tabelą dla wybranego punktu.

## Konfiguracja

1. W HACS dodaj `BartoszWu/tauron-ebok`, typ Integracja; pobierz i zrestartuj HA.
2. Dodaj TAURON eBOK w Ustawienia → Urządzenia i usługi. Podaj login i hasło.
   Formularz sprawdza tylko logowanie, a po sukcesie zapisuje standardowy config
   entry. Nie uzależnia konfiguracji od historii ani faktur.
3. W istniejącej integracji wybierz **Konfiguruj**, włącz historię i wpisz numer
   PPE domu widoczny w portalu. Jeśli istnieje więcej aktywnych kontekstów, najpierw wybierz umowę.
   Nie trzeba ponownie podawać hasła. Wyłączenie historii zatrzymuje harmonogram.
4. Po wskazaniu punktu rozpoczyna się import od 1 stycznia 2026 do dzisiaj.
   **Odśwież historię zużycia** uruchamia kolejny odczyt ręcznie.

Dom nie jest zakodowany w repozytorium. Identyfikatory wybranej umowy i punktu
przechowuje HA w opcjach. Przed zapytaniem klient sprawdza, czy wybrana umowa
nadal jest aktywna. Serwer otrzymuje tylko wybrany numer PPE; odpowiedź musi
zawierać dokładnie ten jeden punkt. Inna lub zbiorcza odpowiedź jest odrzucana. Nie pobiera
zużycia innych punktów ani starych, nieaktywnych umów.

## Historia zużycia

Portal udostępnia JSON przez **GET**
`/api/sitecore/EnergyConsumption/ByPoint` z parametrami `billingNumber`, `from`,
`to` i pojedynczym `Pods`. Aktywny kontekst odkrywamy przez zalogowaną stronę główną. Nie zależymy od
parsowania odnośników na stronie historii: HTTP i Chrome otrzymują tam różne
warianty HTML. Numer PPE wskazuje się raz w opcjach. Nie kopiujemy sesji Chrome.
CSV nie jest potrzebny.

Z odpowiedzi zachowujemy wyłącznie datę odczytu, techniczny typ odczytu i kWh.
Nie zapisujemy nazwiska, adresu, numeru licznika, stanu licznika, pełnej odpowiedzi
ani adresów z query. Przykład struktury (dane fikcyjne):

```json
{"months": [{"month": "2026-01", "kwh": 123.4,
             "reading_dates": ["2026-01-22"]}]}
```

**Miesiąc oznacza miesiąc daty odczytu**, zgodnie z publicznym skryptem portalu
`MeterReadingTransformer.transformPointsAggregated`. Kilka odczytów w tym samym
miesiącu jest sumowanych. Nie jest to gwarancja dokładnego zużycia od pierwszego
do ostatniego dnia miesiąca; nie znamy początku każdego okresu. Daty źródłowe
pozostają w danych. Nie rozdzielamy zużycia proporcjonalnie na dni. Brakujących
miesięcy nie zamieniamy na zero. Brak kWh w odczycie oznacza nieustaloną sumę
miesiąca, nie zero. Typ odczytu zachowujemy bez zgadywania jego znaczenia.

### Encje

- **Zużycie w zaimportowanej historii** — suma rozpoznanych miesięcy; atrybut
  `months` zawiera miesięczne kWh i daty odczytów, `month_count` liczbę miesięcy.
- **Zużycie w ostatnim dostępnym miesiącu** — kWh oraz atrybut `month` i daty.
- **Status historii zużycia** — nie skonfigurowano / nie sprawdzano / pobieranie /
  dane zapisane / odczytano / błąd. `last_error` zawiera wyłącznie stały kod.
- **Ostatni poprawny import historii** — czas ostatniego odczytu i zapisu.
- **Odśwież historię zużycia** — przycisk ręcznego importu.

Historia i czas sukcesu są zapisywane prywatnym, atomowym mechanizmem HA Store,
a następnie odczytywane kontrolnie. Przy błędzie API lub zapisu poprzedni snapshot
pozostaje dostępny, z jawnym statusem błędu i datą ostatniego sukcesu. Po restarcie
cache przywraca dane. Pole `last_check_success` opisuje wynik ostatniego odczytu
w tym uruchomieniu; czas ostatniego sukcesu pozwala ocenić wiek danych.

Odświeżanie odbywa się **pierwszego dnia miesiąca o 06:00 w strefie czasowej HA**.
Nie ma codziennego odpytywania. Jeśli HA ominął miesiąc, start nadrabia odczyt raz;
restart w miesiącu poprawnego importu korzysta z cache. Nieudany zaplanowany
odczyt można ponowić przyciskiem; następny termin automatyczny jest za miesiąc.
Kolejne importy zastępują bieżący rok bez duplikatów, pozostawiając starsze lata.
W styczniu odświeżany jest też poprzedni rok dla opóźnionych odczytów/korekt.

AMIplus pozostaje osobnym źródłem szczegółowych pomiarów. Nie sumujemy jego
wartości z historią eBOK. Sensory archiwum nie deklarują narastającego licznika
ani nie importują statystyk do panelu Energia. Wykres/dashboard jest osobnym
etapem; dane miesięczne są gotowe do jego zasilenia.

## Faktury — oddzielna funkcja eksperymentalna

Dotychczasowe sensory dokumentów i przycisk **Sprawdź dokumenty** pozostają
oddzielne. Nie są uruchamiane przy konfiguracji, imporcie historii ani według
harmonogramu. Ich API pozostaje do osobnej walidacji; nie używaj tego przycisku
jako importu zużycia. Kwota brutto dokumentu nie oznacza należności do zapłaty.
Ta wersja nie pobiera PDF, nie archiwizuje faktur i nie wysyła powiadomień.

## Bezpieczeństwo

- Dane logowania przechowuje standardowy config entry HA. Hasło nie jest
  dodatkowo szyfrowane; chroń dostęp do HA i kopii zapasowych. Nie trafia do Git.
- Sesja HTTP jest tylko w pamięci i oddzielona od wspólnej sesji HA. Po utracie
  sesji klient loguje się zapisanymi danymi i ponawia cały odczyt jeden raz.
- Tylko HTTPS i hosty `moj.tauron.pl`, `logowanie.tauron.pl`; POST wyłącznie do
  logowania SSO. Historia jest pobierana przez GET. MFA/CAPTCHA przerywa próbę.
- Nie wykonujemy płatności, zmian umów, zgód, usług ani zgłoszeń odczytu licznika.
- Bez cookies, haseł, nagłówków autoryzacji, pełnych odpowiedzi i prywatnych URL
  w logach/diagnostyce. Nie włączaj globalnego debugowania ruchu HTTP.
- Usunięcie wpisu integracji usuwa również jej własny magazyn historii.

## Testy

Python 3.13+; bez konta i sieci:

```bash
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

Testy na rzeczywistych klasach HA 2026.10.2 (Python 3.14), w osobnym środowisku:

```bash
python -m pip install homeassistant==2026.10.2
python -m unittest discover -s tests/ha -v
```

Oba zestawy uruchamia GitHub Actions. Obejmują logowanie, ograniczenia transportu,
wybór punktu, grupowanie według dat odczytu, korekty bez duplikatów, zachowanie
cache po błędzie, odtworzenie z prawdziwego HA Store i harmonogram miesięczny.
Fixtures są syntetyczne; testy nie wykorzystują konta ani dokumentów użytkownika.

Źródła: [publiczny skrypt portalu](https://moj.tauron.pl/scripts/script.js),
[AMIplus](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus),
[HA config flows](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/),
[HA fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/).
