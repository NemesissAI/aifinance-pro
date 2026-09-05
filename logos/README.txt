Bank logos
==========

Drop the logo sheet here as:

    logos/turkish-banks.png

That is the 28-logo grid image. It gets sliced into one file per bank
(logos/ziraat.png, logos/garanti.png, logos/kuveyt-turk.png, ...) and the
dashboard picks them up automatically.

Individual files also work if you have them. Name them exactly:

    ziraat.png        garanti.png       kuveyt-turk.png
    akbank.png        isbank.png        yapikredi.png
    vakifbank.png     halkbank.png      denizbank.png
    teb.png           ing.png           hsbc.png
    finansbank.png    sekerbank.png     odeabank.png
    albaraka.png      turkiye-finans.png  vakif-katilim.png
    ziraat-katilim.png  anadolubank.png   fibabanka.png
    burgan.png        aktifbank.png     abank.png
    icbc.png          turkishbank.png   tbank.png
    bankasya.png

PNG with a transparent or white background, any size — they are scaled to
fit a 22px-tall chip. Nothing is fetched from the internet; only files in
this folder are used, and a bank with no file falls back to a coloured
monogram chip.
