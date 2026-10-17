# TAURON eBOK

Eksperymentalna, niezależna integracja Home Assistant do odczytu metadanych
faktur z Mój TAURON. Nieoficjalny projekt, niezwiązany z TAURON.

**Status 0.1.1: implementacja lokalna, bez testu logowania na rzeczywistym
koncie i bez wdrożenia do Home Assistant.** Testy syntetyczne nie dowodzą
zgodności z aktualnym HTML logowania ani archiwum. Repozytorium: [BartoszWu/tauron-ebok](https://github.com/BartoszWu/tauron-ebok).
Wydanie służy do kontrolowanego testu konfiguracji i odczytu danych.

## Diagnostyka konfiguracji

Wersja 0.1.1 rozróżnia błędy odpowiedzi logowania, archiwum/kontekstu klienta
oraz listy dokumentów. Komunikat wskazuje etap, nie udowodnioną przyczynę.
Nie zapisuje żadnych dodatkowych logów, odpowiedzi HTTP ani sekretów.
Po błędzie zachowuje login w formularzu; hasło trzeba wpisać ponownie.
Nie naprawia jeszcze nieustalonej niezgodności z portalem na rzeczywistym koncie.

## Zakres pierwszej wersji

- Formularz konfiguracji w HA: login i hasło, weryfikacja przed zapisaniem wpisu.
- Odrębna sesja HTTP w pamięci, bez Chrome i bez kopiowania cookies przeglądarki.
- Próba logowania formularzem Keycloak po utracie sesji; jedna próba na skan.
- Odczyt wszystkich stron dokumentów z ostatnich 180 dni, dla wykrytych kontekstów
  klienta. Zakres dat i liczba klientów są atrybutami sensora liczby dokumentów.
- Odrzucenie niekompletnych stron, powtórzeń, zmian liczby wyników i dokumentów
  przypisanych do innego klienta. Błąd skanu oznacza niedostępność encji.
- Aktualizacja co 24 godziny oraz przy uruchomieniu integracji.
- Ponowne podanie hasła przez przepływ reautoryzacji Home Assistant.
- Polskie i angielskie nazwy oraz komunikaty formularzy.

Encje pokazują liczbę dokumentów w badanym okresie, datę wystawienia, kwotę
brutto, termin i status ostatniego dokumentu oraz ostatnie poprawne sprawdzenie.
Przy wielu kontekstach ostatni dokument jest wybierany spośród wszystkich.
Przy identycznej dacie rozstrzyga techniczny klucz, nie czas wystawienia.
Jeżeli brakuje daty wystawienia któregoś dokumentu, ostatni dokument pozostaje
nieustalony. Pusty poprawny wynik to liczba 0 i nieznane pola ostatniego dokumentu.

Ta wersja nie pobiera PDF, nie zapisuje archiwum ani rejestru widzianych
dokumentów i nie emituje powiadomień o nowych fakturach. Nie udostępnia salda
ani wyliczonej sumy zaległości: kontrakt tych danych nie został zweryfikowany.
Nie utożsamia kwoty ostatniej faktury z kwotą do zapłaty.

## Instalacja przez HACS

1. HACS → menu → Niestandardowe repozytoria.
2. Dodaj `BartoszWu/tauron-ebok`, typ **Integracja**.
3. Pobierz TAURON eBOK. Po instalacji nowej integracji uruchom ponownie HA
   w dogodnym momencie — restart nie jest wykonywany przez tę integrację.
4. Ustawienia → Urządzenia i usługi → Dodaj integrację → TAURON eBOK.
5. Wpisz login i hasło samodzielnie. Formularz sprawdzi logowanie i listę faktur.

Jeżeli zobaczysz błąd rozpoznawania portalu, przerwij ponawianie konfiguracji
i zgłoś sam komunikat błędu; nie przesyłaj hasła, cookies ani pełnej odpowiedzi.

## Konfiguracja i prywatność

Po zatwierdzonym zainstalowaniu: Ustawienia → Urządzenia i usługi → Dodaj
integrację → TAURON eBOK. Podaj dane do Mój TAURON, nie zakładaj, że konto
eLicznika jest tym samym kontem. Hasło zapisuje mechanizm config entries HA;
trzeba chronić dostęp do HA i jego kopie zapasowe. Integracja nie dodaje osobnego
magazynu haseł ani trwałego magazynu cookies. Po restarcie loguje się ponownie.

Nie loguje treści odpowiedzi, identyfikatorów klienta, adresów URL z query,
haseł ani cookies. Wyjątki transportowe są zastępowane stałymi komunikatami.
Nie włączaj globalnych logów ruchu HTTP podczas testu na prawdziwym koncie.
Sesja jest odłączana bez zamykania współdzielonego konektora HA.

Dozwolone są tylko HTTPS i jawnie wymienione hosty `moj.tauron.pl` oraz
`logowanie.tauron.pl`. Klient wykonuje GET archiwum/listy i POST formularza
logowania. Nie ma kodu płatności, zmiany umowy, zgód, danych konta ani odczytów.
Nowy host SSO wymaga weryfikacji i aktualizacji listy. MFA/CAPTCHA kończy próbę
błędem wymagającym działania użytkownika; integracja ich nie rozwiązuje.

## Potwierdzone i niepotwierdzone elementy

Pasywna analiza zalogowanej przeglądarki potwierdziła GET
`/api/sitecore/EbokArchiveDocuments/GetDocuments`, pola `Documents`, `RangeFrom`,
`RangeTo`, `TotalNumber`, `Status` i `StatusEnum`, zakres strony 1–5 oraz dane
dokumentów użyte w parserze. Widziano także endpoint obrazu dokumentu z PDF
w Base64; jego pobieranie nie jest częścią tego etapu.

Nadal wymagają sprawdzenia na koncie:

1. Przekierowania i formularz logowania HTTP do Mój TAURON. Algorytm oparto na
   publicznym kodzie AMIplus, ale jego obsługa Mój TAURON jest nieaktywna.
2. Sposób osadzenia kontekstów klienta w HTML archiwum. Parser obsługuje pełne
   zestawy `system`, `clientNumber`, `clientNumberCheckSum` w atrybutach,
   linkach i JSON. Te warianty testowano syntetycznie, nie potwierdzono ich
   rzeczywistego kodowania w HTML portalu. Nie zgaduje ani nie łączy fragmentów
   z różnych klientów. Nierozpoznany HTML kończy konfigurację błędem.
3. Trwałość klucza z kontekstu klienta/systemu, typu, numeru technicznego i SodId.
   Klucz jest roboczy; nie należy na nim jeszcze opierać trwałego archiwum.
4. Zachowanie pustych stron, limity zakresów i zmiany listy podczas paginacji.
   Po 100 stronach skan kończy się błędem zamiast zwracać niepełny wynik.
5. Odzyskanie sesji po wygaśnięciu i ewentualne wylogowanie innych sesji przez SSO.

Nie parsuje ilości energii ani stawek bez potwierdzenia jednostek i semantyki.

## Walidacja lokalna

Wymagany Python 3.13.2+ dla docelowego środowiska HA 2026.2+.
Testy klienta używają wyłącznie biblioteki standardowej i fikcyjnych odpowiedzi:

```bash
python -m unittest discover -s tests -v
python -m compileall -q custom_components
git diff --check
```

Pełna walidacja formularzy, encji, startu i unload wymaga środowiska Home
Assistant. Testy transportu stosują podstawiony interfejs sesji i nie testują
samej biblioteki aiohttp. Nie wymagają dostępu do konta ani sieci.

## Kolejny etap

Po osobnej zgodzie: kontrolowana instalacja, samodzielne podanie danych przez
użytkownika i test logowania, wykrywania kont oraz odczytu listy. Następnie
uzupełnienie adapterów na podstawie rzeczywistego schematu, weryfikacja korekt
i identyfikatorów, a dopiero potem lokalne archiwum PDF i wykrywanie nowości.

Usunięcie integracji z UI HA usuwa jej wpis konfiguracji i encje. Nie trzeba
zmieniać AMIplus. Ta wersja nie tworzy archiwum dokumentów do sprzątania.

Źródła: [AMIplus connector.py](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Tauron-AMIplus/blob/master/custom_components/tauron_amiplus/connector.py),
[HA config flows](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/),
[HA fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/).
