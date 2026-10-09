"""Dane materiałowe SAP (eksport BW „SAP_Dane_materialowe”): hierarchia asortymentu,
producent, przeliczniki jednostek z objętościami i wagami — per MATNR.

Łącznik między kodami: zadania EWM niosą MATNR, katalog GROOVE (`Product.code`) — REF.
Źródło danych do projektowania magazynu (grupy do prognozy, sztuki → kartony → palety);
`Product` pozostaje nietknięty.
"""
from django.db import models

__all__ = ["MaterialMaster"]


class MaterialMaster(models.Model):
    matnr = models.CharField(max_length=18, unique=True, verbose_name="MATNR (bez zer wiodących)")
    ref = models.CharField(max_length=60, blank=True, default="", db_index=True, verbose_name="REF")
    name = models.CharField(max_length=250, blank=True, default="", verbose_name="Nazwa")
    kind = models.CharField(max_length=4, blank=True, default="", verbose_name="Rodzaj materiału")
    hierarchy = models.CharField(max_length=18, blank=True, default="", db_index=True,
                                 verbose_name="Hierarchia (kod)")
    h1 = models.CharField(max_length=120, blank=True, default="", verbose_name="H1 — asortyment")
    h2 = models.CharField(max_length=120, blank=True, default="", verbose_name="H2")
    h3 = models.CharField(max_length=120, blank=True, default="", verbose_name="H3")
    h4 = models.CharField(max_length=120, blank=True, default="", verbose_name="H4")
    producer = models.CharField(max_length=120, blank=True, default="", verbose_name="Producent")
    cn_code = models.CharField(max_length=12, blank=True, default="", verbose_name="Kod CN")
    purchase_group = models.CharField(max_length=10, blank=True, default="", verbose_name="Grupa zaopatrzeniowa")
    base_unit = models.CharField(max_length=6, blank=True, default="", verbose_name="JP")
    pcs_per_opz = models.FloatField(null=True, blank=True, verbose_name="Sztuk w OPZ")
    pcs_per_carton = models.FloatField(null=True, blank=True, verbose_name="Sztuk w kartonie")
    pcs_per_pallet = models.FloatField(null=True, blank=True, verbose_name="Sztuk na palecie")
    vol_unit_dm3 = models.FloatField(null=True, blank=True, verbose_name="Objętość sztuki [dm³]")
    vol_carton_dm3 = models.FloatField(null=True, blank=True, verbose_name="Objętość kartonu [dm³]")
    vol_pallet_dm3 = models.FloatField(null=True, blank=True, verbose_name="Objętość palety [dm³]")
    weight_unit_kg = models.FloatField(null=True, blank=True, verbose_name="Waga sztuki [kg]")
    weight_carton_kg = models.FloatField(null=True, blank=True, verbose_name="Waga kartonu [kg]")
    weight_pallet_kg = models.FloatField(null=True, blank=True, verbose_name="Waga palety [kg]")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["matnr"]
        verbose_name = "Dane materiałowe SAP"
        verbose_name_plural = "Dane materiałowe SAP"

    def __str__(self):
        return f"{self.matnr} {self.ref}"
