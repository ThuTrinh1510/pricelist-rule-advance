from odoo import api, fields, models, tools, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.safe_eval import safe_eval


class PricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    x_advance_domain = fields.Char(string='Advance Price Domain', help='Advance Price Domain')
    x_use_text_formula = fields.Boolean(string='Use Text Formula', help='Use Text Formula')
    x_text_formula = fields.Char(string='Text Formula', help='Text Formula', placeholder='e.g.  price * 1.2')

    @api.onchange('compute_price')
    def _onchange_compute_price(self):
        if self.compute_price != 'formula':
            self.x_use_text_formula = False            

    def _is_applicable_for(self, product, qty_in_product_uom):
        res = super(PricelistItem, self)._is_applicable_for(product, qty_in_product_uom)
        if not res or not self.x_advance_domain:
            return res
        product_list = self.env['product.product'].search(safe_eval(self.x_advance_domain))
        if product in product_list:
            res = True
        else:
            res = False
        return res
    
    def _compute_price(self, product, quantity, uom, date, currency=None):
        """Compute the unit price of a product in the context of a pricelist application.

        Note: self and self.ensure_one()

        :param product: recordset of product (product.product/product.template)
        :param float qty: quantity of products requested (in given uom)
        :param uom: unit of measure (uom.uom record)
        :param datetime date: date to use for price computation and currency conversions
        :param currency: currency (for the case where self is empty)

        :returns: price according to pricelist rule or the product price, expressed in the param
                  currency, the pricelist currency or the company currency
        :rtype: float
        """
        self and self.ensure_one()  # self is at most one record
        product.ensure_one()
        uom.ensure_one()

        currency = currency or self.currency_id or self.env.company.currency_id
        currency.ensure_one()

        # Pricelist specific values are specified according to product UoM
        # and must be multiplied according to the factor between uoms
        product_uom = product.uom_id
        if product_uom != uom:
            convert = lambda p: product_uom._compute_price(p, uom)
        else:
            convert = lambda p: p

        if self.compute_price == 'fixed':
            price = convert(self.fixed_price)
        elif self.compute_price == 'percentage':
            base_price = self._compute_base_price(product, quantity, uom, date, currency)
            price = (base_price - (base_price * (self.percent_price / 100))) or 0.0
        elif self.compute_price == 'formula':
            base_price = self._compute_base_price(product, quantity, uom, date, currency)
            # complete formula
            price_limit = base_price

            if self.x_use_text_formula and self.x_text_formula:
                formula = self.x_text_formula
                base_price = safe_eval(formula, {"__builtins__": {}}, {"price": base_price})

            price = (base_price - (base_price * (self.price_discount / 100))) or 0.0
            if self.price_round:
                price = tools.float_round(price, precision_rounding=self.price_round)

            if self.price_surcharge:
                price += convert(self.price_surcharge)

            if self.price_min_margin:
                price = max(price, price_limit + convert(self.price_min_margin))

            if self.price_max_margin:
                price = min(price, price_limit + convert(self.price_max_margin))
        else:  # empty self, or extended pricelist price computation logic
            price = self._compute_base_price(product, quantity, uom, date, currency)

        return price

    @api.depends_context('lang')
    @api.depends('compute_price', 'price_discount', 'price_surcharge', 'base', 'price_round', 'x_text_formula',
                 'x_use_text_formula')
    def _compute_rule_tip(self):
        base_selection_vals = {elem[0]: elem[1] for elem in self._fields['base']._description_selection(self.env)}
        self.rule_tip = False
        for item in self:
            if item.compute_price != 'formula':
                continue
            base_amount = 100
            sub_rule_tip = tools.format_amount(item.env, 100,
                                           item.currency_id)
            if item.x_use_text_formula and item.x_text_formula:
                sub_rule_tip = sub_rule_tip.replace('100', item.x_text_formula.replace('price', '100'))
                base_amount = safe_eval(item.x_text_formula, {"__builtins__": {}}, {"price": 100})
            discount_factor = (100 - item.price_discount) / 100
            discounted_price = base_amount * discount_factor
            if item.price_round:
                discounted_price = tools.float_round(discounted_price, precision_rounding=item.price_round)
            surcharge = tools.format_amount(item.env, item.price_surcharge, item.currency_id)
            item.rule_tip = _(
                "%(base)s with a %(discount)s %% discount and %(surcharge)s extra fee\n"
                "Example: %(amount)s * %(discount_charge)s + %(price_surcharge)s → %(total_amount)s",
                base=base_selection_vals[item.base],
                discount=item.price_discount,
                surcharge=surcharge,
                amount=sub_rule_tip,
                discount_charge=discount_factor,
                price_surcharge=surcharge,
                total_amount=tools.format_amount(
                    item.env, discounted_price + item.price_surcharge, item.currency_id),
            )

    @api.onchange('x_text_formula')
    def _onchange_x_text_formula(self):
        for rec in self:
            if rec.x_text_formula:
                if 'price' not in rec.x_text_formula:
                    raise UserError(
                        _("Formula must contain the word 'price'. Please check the formula syntax. Example: price * 1.2"))
                try:
                    safe_eval(self.x_text_formula, {"__builtins__": {}}, {"price": 100})
                except Exception:
                    raise UserError(_("Formula is invalid or has syntax errors. \nPlease check the formula syntax. Example: price * 1.2"))