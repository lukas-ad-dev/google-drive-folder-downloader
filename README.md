# Google Drive Folder Downloader

Ein kleines Windows-Tool, mit dem man komplette, öffentlich geteilte Google-Drive-Ordner auf einmal herunterladen kann. Link einfügen, scannen, fertig.

Ich habe das für mich gebaut, weil ich öfter größere Ordner mit Handbüchern oder Datensätzen bekommen habe und keine Lust hatte, jede Datei einzeln anzuklicken. Vielleicht ist es für den einen oder anderen nützlich.

## Download

Die aktuelle EXE gibt es unter [Releases](../../releases/latest). Einfach `GoogleDriveFolderDownloader.exe` herunterladen und starten. Es muss nichts installiert werden.

Da die EXE nicht signiert ist, kann Windows SmartScreen warnen. Dann auf „Weitere Informationen“ und „Trotzdem ausführen“ klicken.

## Benutzung

1. In Google Drive den Ordner freigeben: **Freigeben → Jeder mit dem Link**.
2. Den Link kopieren und im Tool einfügen.
3. Auf **Scannen** klicken. Unterordner werden automatisch mitgenommen.
4. Die Dateien sind danach alle vorausgewählt. Mit **Alles herunterladen** kommt der komplette Ordner, oder du hakst einzelne Dateien ab beziehungsweise an.
5. Der Zielordner ist standardmäßig `Downloads\GoogleDrive\<Ordnername>` und kann oben geändert werden.

Unterstützt werden Ordner-Links, einzelne Datei-Links sowie Google Docs, Sheets und Slides. Docs werden als DOCX, Sheets als XLSX und Slides als PPTX gespeichert. Große Dateien mit der Google-Bestätigungsseite funktionieren auch.

Private Links, für die man bei Google eingeloggt sein muss, gehen nicht. Das Tool nutzt absichtlich weder Google-API noch Login, deswegen braucht es auch keinen API-Key.

## Selbst bauen

Voraussetzung ist Python 3.9 oder neuer.

```bat
pip install -r requirements.txt
build.bat
```

Die EXE landet danach in `dist\GoogleDriveFolderDownloader.exe`. Alternativ direkt:

```bat
pyinstaller --noconfirm --onefile --windowed --icon icon.ico --add-data "icon.ico;." --version-file version_info.txt --name GoogleDriveFolderDownloader gdrive_downloader.py
```

## Release über GitHub Actions

Der Workflow in `.github/workflows/build-exe.yml` baut bei jedem Push eine EXE und legt sie als Artifact ab. Wenn ein Tag wie `v1.0.0` gepusht wird, wird zusätzlich ein Release mit der EXE erstellt:

```bat
git tag v1.0.0
git push origin v1.0.0
```

## Lizenz

MIT. Siehe [LICENSE](LICENSE).

---

Kein offizielles Google-Produkt.
