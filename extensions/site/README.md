# Preserved CON presentation extensions

This directory contains the executable CON presentation behavior retained from
accepted site commit `26907c487efaa2c31bba9d02398aa201ab6f774b` in
`https://github.com/con/centerforopenneuroscience.org.git`.

The two files remain byte-for-byte copies of their accepted source paths:

- `layouts/_partials/extend-footer.html` adds the record-edit handoff that the
  released build adapter binds to the downstream's static editor; and
- `layouts/_shortcodes/rawhtml.html` supports the bespoke graph explorer page.

Their inclusion preserves already-reviewed site inputs and does not assert a
new license. The generic Orinoco framework and Congo theme are template-owned
under `.orinoco-lite/site/`; this directory contains only site-owned extension
behavior.
