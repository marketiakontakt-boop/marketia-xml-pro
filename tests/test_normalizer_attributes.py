import pytest
from lxml import etree
from app.parser.normalizer import normalize_product


def _make_elem(attrs: list[tuple[str, str]]) -> etree._Element:
    xml_str = "<product>"
    xml_str += "<product_id>1</product_id><sku>TEST-001</sku><ean></ean>"
    xml_str += "<price>0</price><purchase_price>0</purchase_price>"
    xml_str += "<tax_rate>23%</tax_rate><weight>0</weight>"
    xml_str += "<width>0</width><height>0</height><length>0</length>"
    xml_str += "<quantity>0</quantity><name>Test</name>"
    xml_str += "<category_name></category_name><manufacturer_name></manufacturer_name>"
    xml_str += "<description></description>"
    xml_str += "<description_extra_1></description_extra_1>"
    xml_str += "<description_extra_2></description_extra_2>"
    xml_str += "<attributes>"
    for name, value in attrs:
        xml_str += f"<attribute><attribute_name>{name}</attribute_name><attribute_value>{value}</attribute_value></attribute>"
    xml_str += "</attributes></product>"
    return etree.fromstring(xml_str)


def test_attributes_parsed_from_xml():
    elem = _make_elem([("Waga", "3.5"), ("Kolor", "Niebieski")])
    p = normalize_product(elem)
    assert p.attributes == {"Waga": "3.5", "Kolor": "Niebieski"}


def test_empty_attributes():
    elem = _make_elem([])
    p = normalize_product(elem)
    assert p.attributes == {}


def test_no_attributes_element():
    xml_str = "<product><product_id>1</product_id><sku>T</sku><ean></ean>"
    xml_str += "<price>0</price><purchase_price>0</purchase_price>"
    xml_str += "<tax_rate>23%</tax_rate><weight>0</weight>"
    xml_str += "<width>0</width><height>0</height><length>0</length>"
    xml_str += "<quantity>0</quantity><name>T</name>"
    xml_str += "<category_name></category_name><manufacturer_name></manufacturer_name>"
    xml_str += "<description></description>"
    xml_str += "<description_extra_1></description_extra_1>"
    xml_str += "<description_extra_2></description_extra_2>"
    xml_str += "</product>"
    elem = etree.fromstring(xml_str)
    p = normalize_product(elem)
    assert p.attributes == {}
