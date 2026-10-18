# TAURON eBOK

Eksperymentalna integracja Home Assistant do Mój TAURON, niezwiązana z TAURON.

**0.2.0: najpierw logowanie i zapis danych w HA, potem osobne sprawdzanie
 dokumentów.** Logowanie na rzeczywistym koncie nadal wymaga potwierdzenia
przez użytkownika. Błąd API dokumentów nie blokuje już konfiguracji.

## Konfiguracja

1. W HACS dodaj niestandardowe repozytorium `BartoszWu/tauron-ebok`, typ Integracja.
2. Pobierz TAURON eBOK i uruchom ponownie Home Assistant.
3. Zamknij poprzedni formularz, odśwież stronę HA i wybierz Ustawienia →
   Urządzenia i usługi → Dodaj integrację → TAURON eBOK.
4. Wpisz samodzielnie login i hasło do Mój TAURON.

Formularz wykonuje logowanie HTTP i odczytuje stronę główną. Sprawdza
przekierowanie, brak formularza logowania oraz link wylogowania na stronie
zalogowanego użytkownika. Nie pobiera archiwum, listy umów, faktur ani PDF.
Nie zapisuje danych, jeżeli nie potwierdzi zalogowanej strony.

Po sukcesie zapisuje dane standardowym mechanizmem config entries HA. Hasło nie
jest dodatkowo szyfrowane na dysku; chroń dostęp do HA i jego kopie zapasowe.
Dane pozostają w HA po restarcie i aktualizacji integracji. Nie trzeba ich
wpisywać ponownie przy każdym sprawdzeniu dokumentów. Po błędnym haśle lub
zmianie zabezpieczeń może być potrzebna reautoryzacja.

Start/restart integracji nie odpytuje portalu ani dokumentów. Błąd parsera
faktur nie powoduje utraty zapisanego konta.

## Sprawdź dokumenty — osobny krok

W urządzeniu TAURON eBOK naciśnij **Sprawdź dokumenty**. Przycisk uruchamia
jeden skan listy dokumentów z ostatnich 180 dni, z paginacją dla wykrytych
kontekstów klienta. W razie potrzeby klient sam loguje się zapisanymi danymi.
Przycisk można też wywołać w automatyzacji przez natywną akcję `button.press`,
wybierając rzeczywistą encję utworzoną przez HA.

**Nie ma automatycznego dziennego odpytywania.** Harmonogram można dodać później
osobną automatyzacją. Samo dodanie integracji nie uruchamia tego przycisku.

Status odczytu dokumentów:

- **Nie sprawdzano** — nie wykonano jeszcze skanu w tym uruchomieniu.
- **Sprawdzanie** — trwa żądanie.
- **Odczytano** — skan zakończony poprawnie.
- **Błąd** — skan niekompletny lub nieudany; atrybut `last_error` podaje stały kod
  etapu, np. `portal_archive`, `portal_documents`, `cannot_connect`, `invalid_auth`.

Błąd nie usuwa konfiguracji ani hasła. Ponowne naciśnięcie przycisku ponawia
skan. Nie włączaj globalnego logowania HTTP i nie przesyłaj pełnych odpowiedzi,
haseł ani cookies. Poprawne działanie API dokumentów pozostaje osobnym etapem
weryfikacji — ta wersja rozdziela je od logowania.

Encje metadanych: liczba dokumentów w okresie, data wystawienia, kwota brutto,
termin i status ostatniego dokumentu, ostatni poprawny odczyt. Do czasu pierwszego
skanu metadane są niedostępne, nie pokazują fikcyjnego zera. Po nieudanym skanie
metadane są niedostępne; status błędu i czas ostatniego sukcesu pozostają widoczne.
Przy wielu kontekstach ostatni dokument jest wybierany spośród wszystkich.
Przy identycznej dacie rozstrzyga techniczny klucz, nie czas wystawienia. Brak
którejkolwiek daty wystawienia pozostawia ostatni dokument nieustalony.

## Bezpieczeństwo i ograniczenia

- Własna sesja HTTP tylko w pamięci. Bez Chrome, importowania cookies
  przeglądarki i dodatkowego magazynu haseł.
- HTTPS i jawna lista hostów: `moj.tauron.pl`, `logowanie.tauron.pl`.
- POST tylko do formularza logowania. Odczyt strony głównej, archiwum i listy
  przez GET. Bez płatności, zmian umowy, danych konta, zgód i odczytów licznika.
- MFA/CAPTCHA kończy próbę błędem; integracja ich nie rozwiązuje.
- Brak logowania danych konta, cookies, pełnych odpowiedzi i adresów z query.
  Wyjątki transportu zastępowane są stałymi komunikatami.
- Parser sprawdza kompletność stron, właściciela dokumentu, powtórzenia i zmianę
  liczby wyników podczas skanu. Limit 100 stron zamiast niepełnego sukcesu.
- Potwierdzono nazwy `data-client-number`, `data-client-checksum`, `data-system`
  w HTML/publicznym skrypcie. Zasady unikalności ID, pełną paginację rzeczywistych
  dokumentów i konta z większą liczbą umów trzeba jeszcze zweryfikować.
- Brak PDF, lokalnego archiwum, powiadomień, salda i wyliczania zaległości.
  Kwota ostatniego dokumentu nie oznacza kwoty do zapłaty.

Usunięcie integracji przez UI HA usuwa jej konfigurację i encje. AMIplus jest
osobną integracją i nie jest zmieniany. Ta wersja nie tworzy archiwum PDF.

## Testy

Testy klienta bez zależności i sieci:

```bash
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

Testy na rzeczywistych klasach HA 2026.10.2 (Python 3.14), z fikcyjnymi danymi
i klientem bez dostępu do portalu, w oddzielnym środowisku:

```bash
python -m pip install homeassistant==2026.10.2
python -m unittest discover -s tests/ha -v
```

Sprawdzają utworzenie wpisu po samym logowaniu, odrzucenie błędnego hasła,
start bez pobierania dokumentów, ręczny przycisk, zachowanie konfiguracji po
błędzie i ponowienie odczytu. Oba zestawy uruchamia GitHub Actions.

Źródła: [AMIplus connector.py](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/blob/master/custom_components/tauron_amiplus/connector.py),
[HA config flows](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/),
[HA fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/),
[HA button](https://developers.home-assistant.io/docs/core/entity/button/).
