# JS8Mesh Εγχειρίδιο Χρήστη

## 1. Τι Είναι Το JS8Mesh

Το JS8Mesh είναι ένα εργαλείο human-supervised mesh-awareness και relay για το JS8Call.

Δεν είναι αυτοματοποιημένο routing mesh network. Κάθε μετάδοση εξακολουθεί
να εξαρτάται από έναν ανθρώπινο operator που αποφασίζει τι θα στείλει και πότε
θα το στείλει.

Το JS8Mesh σας βοηθά να:
- βλέπετε ποιος ακούει ποιον
- συγκρίνετε πιθανά relay paths
- προετοιμάζετε relay text για το JS8Call
- δημιουργείτε mesh reports
- απαντάτε σε υποστηριζόμενα `JC` requests με έτοιμες prepared replies

## 2. Important Disclaimer

Χρησιμοποιήστε το σύμφωνα με τους τοπικούς νόμους σας. Χρήση με δική σας ευθύνη.

Ο operator είναι υπεύθυνος για όλες τις μεταδόσεις.

Το JS8Mesh δημιουργήθηκε με τη βοήθεια AI και πρέπει να θεωρείται experimental
software.

Να επιβλέπετε πάντα τον σταθμό σας.

## 3. What You Need

- Windows ή Linux
- JS8Call εγκατεστημένο και λειτουργικό
- ένα έγκυρο callsign ρυθμισμένο στο JS8Mesh
- activity/traffic στο JS8Call ώστε το JS8Mesh να έχει δεδομένα για να δουλέψει

Προαιρετικά αλλά συνιστάται:
- `JS8Call Control` ενεργοποιημένο στο JS8Mesh αν θέλετε το JS8Mesh να
  στέλνει/stage text προς το JS8Call αυτόματα

Αν τρέχετε το JS8Mesh από source αντί για packaged release, θα χρειαστείτε
επίσης:
- Python
- `pyjs8call`
- Tk/Tkinter support
- να τρέξετε την εφαρμογή από το `main.py`

Compatibility note:
- το JS8Mesh έχει σχεδιαστεί για JS8Call versions που παρέχουν
  `DIRECTED.TXT`-compatible directed-message output
- δεν πρέπει να θεωρείται δεδομένη η συμβατότητα με κάθε παλιότερη JS8Call
  version ή κάθε fork/build, εκτός αν υπάρχει αυτή η συμπεριφορά και είναι
  συμβατή

Συνήθης τοποθεσία του `DIRECTED.TXT` στα Windows:
- `%LOCALAPPDATA%\JS8Call\DIRECTED.TXT`
- συνήθως αντιστοιχεί σε `C:\Users\<YourUser>\AppData\Local\JS8Call\DIRECTED.TXT`

## 4. First Start

Important setup notes:
- ξεκινήστε πρώτα το JS8Call και μετά το JS8Mesh
- προσθέστε το `@JS8MESH` στα JS8Call groups σας

Όταν ανοίξει το JS8Mesh για πρώτη φορά:

1. Ανοίξτε το `Settings`.
2. Ρυθμίστε τα callsign information σας.
3. Βρείτε το `DIRECTED.TXT`.
4. Επιλέξτε ή προσθέστε το operating frequency που θέλετε.
5. Ρυθμίστε το maximum TX time σε κάθε σημείο που εμφανίζεται στο `Settings`.
6. Αποφασίστε αν το `JS8Call Control` θα είναι `YES` ή `NO`.

Αν το `JS8Call Control` είναι `YES`, το JS8Mesh μπορεί να:
- φορτώνει prepared text στο JS8Call
- στέλνει προς το JS8Call μέσω του integration path του
- προσπαθεί να αλλάξει το JS8Call speed mode πριν το send
- προσπαθεί να επαναφέρει το προηγούμενο mode μετά τη μετάδοση

Αν το `JS8Call Control` είναι `NO`, το JS8Mesh μόνο προετοιμάζει/stage text
και εσείς παραμένετε πλήρως manual στο JS8Call.

## 5. Main Window

Το main window είναι οργανωμένο γύρω από μερικές βασικές περιοχές:

- top controls
  - user callsign
  - target callsign
  - operating frequency
  - saved settings
- `Linear Pathways`
  - recommended relay routes προς το current target
- `Inbound Reachability`
  - stations που μπορεί να βοηθήσουν inbound convergence
- `RX/TX Monitor`
  - view του πρόσφατου JS8Call decoded και transmitting activity από το `ALL.TXT`
- `Relay Profiles`
  - summary view γνωστών relay stations
- `Topology`
  - traffic ή mesh structure view
- `Activity`
  - decoded records που έχει δει το JS8Mesh
- `Relay Message Builder`
  - προετοιμασία relay messages για το JS8Call

## 6. Main Concepts

### Nodes and Stations

- `node`
  - ένας station που χρησιμοποιεί JS8Mesh και συμμετέχει σε structured mesh reports
- `station`
  - οποιοδήποτε callsign φαίνεται/ακούγεται στα records, ακόμη κι αν δεν
    χρησιμοποιεί JS8Mesh

### Linear Pathway

Ένα `Linear Pathway` είναι ένα relay route που το JS8Mesh θεωρεί usable με βάση
τα stored hearing/report evidence που έχει.

Σε ένα linear pathway, το stored evidence υποστηρίζει την ιδέα ότι κάθε
station στην αλυσίδα μπορεί να ακούσει τον station πριν από αυτόν και τον
station μετά από αυτόν με τρόπο που ταιριάζει στη pathway logic του JS8Mesh.

Γι' αυτό ένα linear pathway είναι ο πιο ασφαλής και πιο trusted τύπος route.
Βασίζεται σε evidence που υποστηρίζει πιο άμεσα ολόκληρη την relay chain.

### Inbound Convergence

Το `Inbound Convergence` είναι διαφορετικό από ένα linear pathway.

Αντιπροσωπεύει έναν station ή route που μπορεί να βοηθήσει τα messages να
συγκλίνουν προς το target, αλλά το route δεν είναι τόσο άμεσα αποδεδειγμένο
όσο ένα native linear pathway.

Άρα:
- `Linear Pathway`
  - ισχυρότερο route evidence
  - πιο ασφαλές για πραγματική relay χρήση
  - προτιμάται όταν είναι διαθέσιμο
- `Inbound Convergence`
  - χρήσιμο hint ή candidate
  - μπορεί αργότερα να γίνει ισχυρότερο με περισσότερο evidence ή manual success
  - λιγότερο άμεσα αποδεδειγμένο από ένα linear pathway

Με απλά λόγια:
- `Linear` σημαίνει ότι η αλυσίδα υποστηρίζεται πιο άμεσα από όσα γνωρίζει το JS8Mesh
- `Inbound` σημαίνει ότι το route μπορεί να βοηθήσει, αλλά δεν είναι τόσο πλήρως αποδεδειγμένο

### Direct

`Direct` σημαίνει ότι το JS8Mesh βλέπει direct path ανάμεσα σε εσάς και στο
target, σύμφωνα με το evidence model του.

### Freshness and SNR

Το JS8Mesh προτιμά:
- πιο fresh evidence
- ισχυρότερο SNR
- πιο σύντομα relay pathways

Το freshness χωρίζεται σε 4 στάδια:
- `0-10 min`
- `10-30 min`
- `30-65 min`
- `>65 min`

Για normal pathway generation, evidence παλαιότερο από 65 minutes θεωρείται
πολύ παλιό για χρήση. Πρακτικά, μετά από αυτό το σημείο ο station/path
θεωρείται QRT για pathway generation, εκτός αν χρησιμοποιείτε Test Mode ή
εμφανιστεί νεότερο evidence.

## 7. Settings

### Callsign

Χρησιμοποιήστε `Settings > Callsign` για να:
- ορίσετε το amateur radio callsign σας
- προαιρετικά να ορίσετε special callsign
- αντιστοιχίσετε αυτό το special callsign σε συγκεκριμένο frequency αν το χρειάζεστε

### Add / Remove Frequency

Χρησιμοποιήστε `Settings > Add / Remove Frequency` για να:
- προσθέσετε custom frequencies
- αφαιρέσετε custom frequencies που δεν θέλετε πλέον

### JS8Call Control

Χρησιμοποιήστε `Settings > JS8Call Control` για να επιλέξετε αν το JS8Mesh θα
πρέπει μόνο να προετοιμάζει text ή και να το δίνει στο JS8Call.

### Sync Frequency from JS8Call

Χρησιμοποιήστε `Settings > Sync Frequency from JS8Call` αν θέλετε το JS8Mesh
να ακολουθεί το frequency που χρησιμοποιείται εκείνη τη στιγμή στο JS8Call.

### Report TX Time Limit

Χρησιμοποιήστε `Settings > Report TX Time Limit` για να ορίσετε το default
transmit-time limit για generated:

- `JR`
- `JRN`
- `JRS`
- `HR`
- `HRC`

Αν μια generated reply ξεπερνά αυτό το limit, το JS8Mesh την trim ή την
αρνείται ανάλογα με την περίπτωση.

Important note:
Το αποτέλεσμα του JS8Mesh TX time estimator μπορεί να διαφέρει από τον πραγματικό
TX time που θα χρειαστεί το JS8Call. Να είστε πάντα προσεκτικοί. Better safe
than sorry. Use at your own risk.

### Watch Callsigns

Χρησιμοποιήστε `Settings > Watch Callsigns` για να προσθέσετε callsigns που
πρέπει να προκαλούν on-screen notification όταν εμφανίζονται σε νέο activity.

### Test Mode

Το `Test Mode` είναι χρήσιμο όταν θέλετε το JS8Mesh να δουλεύει από ένα
περιορισμένο slice πρόσφατου activity αντί για τους normal freshness rules.

Το πεδίο `recent records` σημαίνει:
- τον αριθμό των πιο πρόσφατων lines από το `DIRECTED.TXT` που θα χρησιμοποιεί
  το JS8Mesh όσο το Test Mode είναι ON

Άρα αν το `recent records` είναι `100`, το Test Mode δουλεύει με τα τελευταία
100 decoded records αντί για το normal live freshness window.

Αν το `recent records` είναι `0`, το JS8Mesh θα χρησιμοποιήσει όλο το
`DIRECTED.TXT`. Πρακτικά, αυτό σημαίνει όλες τις πληροφορίες που έχουν ποτέ
σημειωθεί εκεί.

## 8. Mesh Reports Menu

### Request Report

Χρησιμοποιήστε `Mesh Reports > Request Report` για να στείλετε αυτούς τους
request types από το request window:

- `General`
  - στέλνει `JR`
  - general structured mesh report
- `Nodes Only`
  - στέλνει `JRN`
  - nodes only
- `Stations Only`
  - στέλνει `JRS`
  - stations only
- `Heard 4 Stations`
  - στέλνει `HR`
  - direct-heard station snapshot
- `Can Relay to Callsign`
  - στέλνει `HRC`
  - ρωτά αν ο node μπορεί να επικοινωνήσει directly με συγκεκριμένο callsign
- `Find Callsign`
  - στέλνει `FIND`
  - ζητά από έναν node ή group of nodes να παρακολουθούν ένα συγκεκριμένο
    callsign και να αναφέρουν αργότερα αν ακουστεί

Τα requests μπορούν να είναι:
- direct προς node
- relayed προς node

Για το `Find Callsign`:
- direct `FIND` κρατιέται από τον receiving node και δεν γίνεται rebroadcast
- group `@JS8MESH FIND` μπορεί να καθυστερήσει και να γίνει rebroadcast
- held searches διαρκούν 24 ώρες
- αν το target ακουστεί αργότερα και υπάρχει return path, το JS8Mesh προετοιμάζει
  `FINDR`

### TX Mesh Reports

Χρησιμοποιήστε `Settings > TX Mesh Reports` για να ορίσετε:
- station count
- lookback period
- interval ή fixed schedule
- mode
- TX time limit

Όταν ένα scheduled mesh report είναι due, το JS8Mesh προετοιμάζει το report και
δείχνει το confirmation/send flow σύμφωνα με τα current settings.

## 9. Supported Request Types

### JR

Επιστρέφει structured multi-wave mesh report.

Θα περιλαμβάνει τουλάχιστον ένα node αν είναι διαθέσιμο.

### JRN

Ίδιο structured report style με το `JR`, αλλά nodes only.

### JRS

Ίδιο structured report style με το `JR`, αλλά stations only.

### HR

Επιστρέφει έως 4 directly heard stations χρησιμοποιώντας τα JS8Mesh selection criteria.

### HRC

Ρωτά αν ο node μπορεί να επικοινωνήσει directly με ένα συγκεκριμένο callsign.

Αν ναι, το JS8Mesh προετοιμάζει one-line `JR` reply μόνο για αυτό το callsign.

Αν δεν βρεθεί direct communication, δεν δημιουργείται reply.

### FIND

Ζητά από έναν node ή από nodes να παρακολουθούν ένα συγκεκριμένο callsign για
έως 24 ώρες.

Υποστηρίζονται δύο βασικοί τρόποι χρήσης:
- direct προς έναν συγκεκριμένο node
- group request μέσω `@JS8MESH`

Direct `FIND`:
- αποθηκεύεται από τον receiving node για τον εαυτό του
- δεν γίνεται automatic rebroadcast

Group `FIND`:
- μπορεί να αποθηκευτεί από πολλούς receiving nodes
- μπορεί να γίνει rebroadcast μετά από random delay
- χρησιμοποιεί duplicate control ώστε να μην επαναλαμβάνεται άσκοπα το ίδιο search

Αν το searched callsign ακούγεται ήδη locally, το JS8Mesh μπορεί να προετοιμάσει
result αμέσως.

Αν το searched callsign ακουστεί αργότερα και υπάρχει return path, το JS8Mesh
προετοιμάζει `FINDR`.

### FINDR

Το `FINDR` είναι το return message για ένα επιτυχημένο `FIND`.

Αναφέρει ότι το searched callsign ακούστηκε, μαζί με το σχετικό freshness και
SNR information.

### Relayed Requests

Direct και relayed requests υποστηρίζονται για:
- `JR`
- `JRN`
- `JRS`
- `HR`
- `HRC`
- `FIND`

Αν ένα request φτάσει με relay και δημιουργηθεί reply, η reply ακολουθεί το
reverse path.

## 10. How To Read Reports

Εδώ υπάρχουν μερικά απλά παραδείγματα με fake callsigns.

### Example 1: JR

`JR.1.calls1.+10.3;2.calls2.calls1.+8.9;3.calls3.calls2.+6.15`

Διαβάζεται έτσι:
- `1.calls1.+10.3`
  - wave 1
  - το `calls1` αναφέρεται direct πρώτο
  - το `+10` είναι το SNR
  - το `3` σημαίνει περίπου 3 minutes old
- `2.calls2.calls1.+8.9`
  - wave 2
  - το `calls2` βρίσκεται πίσω από το `calls1`
  - το `+8` είναι το SNR
  - το `9` σημαίνει περίπου 9 minutes old
- `3.calls3.calls2.+6.15`
  - wave 3
  - το `calls3` βρίσκεται πίσω από το `calls2`
  - το `+6` είναι το SNR
  - το `15` σημαίνει περίπου 15 minutes old

Άρα η δομή είναι:
- responder -> `calls1` -> `calls2` -> `calls3`

### Example 2: HR

`HR.1.*calls1.+12.2;1.calls2.+9.4;1.calls3.+7.8`

Διαβάζεται έτσι:
- κάθε entry είναι `wave 1`
- πρόκειται για stations που ακούγονται directly από τον responding node
- `*calls1` σημαίνει ότι το `calls1` αναγνωρίζεται ως node
- `+12`, `+9`, `+7` είναι οι τιμές SNR
- `2`, `4`, `8` είναι freshness σε minutes

Άρα αυτό σημαίνει:
- ο responding node ακούει directly τα `calls1`, `calls2` και `calls3`
- το `calls1` είναι node

### Example 3: HRC

`JR.1.calls4.+11.2`

Μια `HRC` reply είναι μια πολύ μικρή targeted reply.

Διαβάζεται έτσι:
- ο requested node μπορεί να επικοινωνήσει directly με το `calls4`
- το evidence είναι μόνο wave 1
- το `+11` είναι το SNR
- το `2` σημαίνει περίπου 2 minutes old

Αν δεν βρεθεί direct communication για το searched callsign, δεν δημιουργείται
`HRC` reply.

## 11. Requested Report Window

Όταν το JS8Mesh μπορεί να προετοιμάσει reply για ένα incoming request, ανοίγει
κατευθείαν το `Requested Report` window.

Αυτό το window δείχνει:
- ποιος ζήτησε το report
- το request path
- το requested target callsign για `HRC`, όταν υπάρχει
- prepared text
- estimated TX time
- default speed mode
- τι θα προσπαθήσει να κάνει το JS8Mesh στο JS8Call πριν το send

Buttons:
- `Send to JS8Call`
- `Copy to Clipboard`
- `Close`

Αν δεν μπορεί να δημιουργηθεί report, το JS8Mesh κάνει skip quietly και
καταγράφει το αποτέλεσμα στο appropriate log όπου υπάρχει.

## 12. Relay Message Builder

Το `Relay Message Builder` σας επιτρέπει να:
- επιλέξετε pathway από τα `Linear Pathways`
- πληκτρολογήσετε message
- δείτε το πλήρως prepared relay text
- επιλέξετε TX mode
- στείλετε/stage το αποτέλεσμα στο JS8Call

Το `Default = ...` text στη γραμμή `TX MODE` δείχνει το default speed mode για
το currently selected pathway.

### Mark Success, Mark Failure, Pending, and ACK

Όταν στέλνετε relay message, το JS8Mesh μπορεί να παρακολουθεί την προσπάθεια
στο `Past Relays Log`.

Πιθανές καταστάσεις:
- `P`
  - pending
- `S`
  - success
- `F`
  - failure

`Pending` σημαίνει ότι το JS8Mesh περιμένει να δει αν θα εμφανιστεί ACK-like reply.

ACK-like positive replies περιλαμβάνουν tokens όπως:
- `RR`
- `QSL`
- `ACK`
- `YES`
- `FB`
- `RGR`
- `ROGER`

Αν το expected ACK φτάσει στη σωστή return direction, το JS8Mesh μπορεί να
σημειώσει το relay automatically ως success.

Αν δεν φτάσει matching ACK εγκαίρως, το relay σημειώνεται automatically ως
failure εκτός αν ο operator το αλλάξει manual νωρίτερα.

Timeout rule:
- το JS8Mesh περιμένει μέχρι να περάσει ο estimated transmission time
- μετά προσθέτει 5-minute ACK recognition window
- μετά από αυτό, το pending relay σημειώνεται ως `F` εκτός αν:
  - είδε matching ACK, ή
  - ο operator το σημείωσε manual ως success ή failure

Ο operator μπορεί επίσης να χρησιμοποιήσει:
- `Mark Success`
- `Mark Failure`

για να κάνει override το pending result manually.

Important:
Το `Mark Success` είναι πολύ χρήσιμο επειδή επηρεάζει το future pathway ranking.
Pathways που έχουν αποδειχθεί successful μπορούν να ανέβουν ψηλότερα στη λίστα,
βοηθώντας το JS8Mesh να προτιμά pathways που δούλεψαν στην πράξη και όχι μόνο
με βάση hearing evidence.

## 13. Topology

Το `Topology` window έχει δύο views:

- `traffic`
  - overall recent traffic observations
- `mesh`
  - decoded mesh structure από reports όπως `JR` και `HR`

Τα mesh wave filters μπορούν να δείχνουν:
- all waves
- exact wave filters
- deeper filters όπως `ONLY 9+`

Incoming `HR` reports γίνονται mirror στο mesh topology ως direct-hearing edges.

## 14. Loggers

Χρησιμοποιήστε `Loggers` για να ανοίξετε:

- `Requested Report Responds Log`
- `TX Mesh Reports Log`
- `HR Log`
- `My Find Searches`
- `Held Find Searches`
- `Past Relays Log`

Αυτά τα windows υποστηρίζουν:
- row selection
- right-click copy
- right-click select all
- export `.txt`
- export `.csv` όπου υπάρχει
- clear log όπου υπάρχει

## 15. RX/TX Monitor

Χρησιμοποιήστε το `RX/TX Monitor` button στο main window για να δείτε recent
JS8Call decoded και transmitting activity μέσα από το JS8Mesh.

Behavior:
- διαβάζει το JS8Call `ALL.TXT` directly αντί να κάνει polling το JS8Call TCP API
- δείχνει recent RX και TX log lines
- ανοίγει στην latest line
- ακολουθεί τα νέα lines στο κάτω μέρος
- σταματά το auto-follow αν κάνετε manual scroll μακριά

## 16. Maintenance

Το JS8Mesh κρατά ορισμένα logs/history και μπορεί περιστασιακά να σας
προτρέπει για maintenance.

Όταν τρέχει maintenance, entries παλαιότερα από 90 days αφαιρούνται από τα
retained log files.

## 17. GitHub / Release Use

Αν χρησιμοποιείτε το packaged `.exe` release:
- δεν χρειάζεται να εγκαταστήσετε ξεχωριστά το `pyjs8call`

Αν τρέχετε το source code:
- χρειάζεστε Python
- χρειάζεστε `pyjs8call`
- χρειάζεστε Tk/Tkinter support
- το source entry point είναι το `main.py`
- στο Linux, πρέπει να περιμένετε ότι θα τρέξετε το JS8Mesh από source εκτός
  αν δημιουργήσετε δικό σας packaged build

Απλά παραδείγματα source-run:

Windows PowerShell:
```powershell
pip install pyjs8call
python main.py
```

Linux:
```bash
python3 -m pip install pyjs8call
python3 main.py
```

## 18. License

Το JS8Mesh είναι licensed under `GPL-3.0-only`.

Modified versions και forks πρέπει να χρησιμοποιούν διαφορετικό όνομα ώστε να
αποφεύγεται σύγχυση με το original project.

Δείτε:
- [LICENSE](LICENSE)
- [NAME_USE.md](NAME_USE.md)

## 19. Credits

- Uses `pyjs8call` for JS8Call integration.
- Τα `"JC"` concepts εμπνεύστηκαν εν μέρει από το JS8Spotter.
