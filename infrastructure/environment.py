# -*- coding: utf-8 -*-
import string
from openerp import models, fields, api, _
from openerp.exceptions import Warning
from fabric.api import cd
from .server import custom_sudo as sudo
from fabric.contrib.files import exists
import os


class environment(models.Model):

    """"""
    _name = 'infrastructure.environment'
    _description = 'environment'
    _inherit = ['ir.needaction_mixin', 'mail.thread']

    _states_ = [
        # State machine: untitle
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('cancel', 'Cancel'),
    ]

    @api.model
    def get_odoo_version(self):
        return self.env['infrastructure.odoo_version'].search([], limit=1)

    number = fields.Integer(
        string='Number',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        )
    name = fields.Char(
        string='Name',
        readonly=True,
        required=True,
        size=16,
        states={'draft': [('readonly', False)]},
        )
    type = fields.Selection([
         (u'docker', u'Docker'),
         ],
        string='Type',
        readonly=True,
        required=True,
        states={'draft': [('readonly', False)]},
        default='docker'
        )
    description = fields.Char(
        string='Description'
        )
    partner_id = fields.Many2one(
        'res.partner',
        string='Partner',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        )
    odoo_version_id = fields.Many2one(
        'infrastructure.odoo_version',
        string='Odoo Version',
        required=True,
        readonly=True,
        default=get_odoo_version,
        states={'draft': [('readonly', False)]},
        )
    note = fields.Html(
        string='Note'
        )
    color = fields.Integer(
        string='Color Index'
        )
    state = fields.Selection(
        _states_,
        string="State",
        default='draft',
        )
    server_id = fields.Many2one(
        'infrastructure.server',
        string='Server',
        ondelete='cascade',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        )
    instance_ids = fields.One2many(
        'infrastructure.instance',
        'environment_id',
        string='Instances',
        context={'from_environment': True},
        )
    path = fields.Char(
        string='Path',
        readonly=True,
        required=True,
        states={'draft': [('readonly', False)]},
        )
    instance_count = fields.Integer(
        string='# Instances',
        compute='_get_instances'
        )
    database_ids = fields.One2many(
        'infrastructure.database',
        'environment_id',
        string='Databases'
        )
    database_count = fields.Integer(
        string='# Databases',
        compute='_get_databases'
        )

    @api.one
    @api.depends('database_ids')
    def _get_databases(self):
        self.database_count = len(self.database_ids)

    @api.one
    @api.depends('instance_ids')
    def _get_instances(self):
        self.instance_count = len(self.instance_ids)

    @api.one
    @api.constrains('number')
    def _check_number(self):
        if not self.number or self.number < 10 or self.number > 99:
            raise Warning(_('Number should be between 10 and 99'))

    @api.one
    def unlink(self):
        if self.state not in ('draft', 'cancel'):
            raise Warning(
                _('You cannot delete a environment which is not \
                    draft or cancelled.'))
        return super(environment, self).unlink()

    @api.onchange('server_id')
    def _get_number(self):
        environments = self.search(
            [('server_id', '=', self.server_id.id)],
            order='number desc',
                )
        if self.server_id.server_use_type:
            self.partner_id = self.server_id.used_by_id
        self.number = environments and environments[0].number + 1 or 10

    @api.onchange('partner_id', 'odoo_version_id')
    def _get_name(self):
        name = False
        if self.partner_id and self.odoo_version_id:
            partner_name = self.partner_id.commercial_partner_id.name
            sufix = self.odoo_version_id.sufix
            name = '%s-%s' % (partner_name, sufix)
            valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
            name = ''.join(c for c in name if c in valid_chars)
            name = name.replace(' ', '').replace('.', '').lower()
        self.name = name

    @api.onchange('name', 'server_id')
    def _get_path(self):
        path = False
        if self.server_id.base_path and self.name:
            path = os.path.join(self.server_id.base_path, self.name)
        self.path = path

    @api.one
    def make_env_paths(self):
        self.server_id.get_env()
        if exists(self.path, use_sudo=True):
            raise Warning(_("Folder '%s' already exists") %
                          (self.path))
        sudo('mkdir -p ' + self.path)

    @api.multi
    def create_environment(self):
        self.make_env_paths()
        self.signal_workflow('sgn_to_active')

    @api.multi
    def delete(self):
        if self.instance_ids:
            raise Warning(_(
                'You can not delete an environment that has instances'))
        self.server_id.get_env()
        paths = [self.path]
        for path in paths:
            sudo('rm -f -r ' + path)
        self.signal_workflow('sgn_cancel')

    @api.multi
    def action_wfk_set_draft(self):
        self.write({'state': 'draft'})
        self.delete_workflow()
        self.create_workflow()
        return True

    _sql_constraints = [
        ('name_uniq', 'unique(name, server_id)',
            'Name must be unique per server!'),
        ('path_uniq', 'unique(path, server_id)',
            'Path must be unique per server!'),
        ('sources_number', 'unique(number, server_id)',
            'Number must be unique per server!'),
    ]

    @api.multi
    def action_view_instances(self):
        '''
        This function returns an action that display a form or tree view
        '''
        instances = self.instance_ids.search(
            [('environment_id', 'in', self.ids)])
        action = self.env['ir.model.data'].xmlid_to_object(
            'infrastructure.action_infrastructure_instance_instances')

        if not action:
            return False
        res = action.read()[0]
        res['domain'] = [('id', 'in', instances.ids)]
        if len(self) == 1:
            res['context'] = {'default_environment_id': self.id}
        if not len(instances.ids) > 1:
            form_view_id = self.env['ir.model.data'].xmlid_to_res_id(
                'infrastructure.view_infrastructure_instance_form')
            res['views'] = [(form_view_id, 'form')]
            # if 1 then we send res_id, if 0 open a new form view
            res['res_id'] = instances and instances.ids[0] or False
        return res

    @api.multi
    def action_view_databases(self):
        '''
        This function returns an action that display a form or tree view
        '''
        databases = self.database_ids.search(
            [('environment_id', 'in', self.ids)])
        action = self.env['ir.model.data'].xmlid_to_object(
            'infrastructure.action_infrastructure_database_databases')

        if not action:
            return False
        res = action.read()[0]
        res['domain'] = [('id', 'in', databases.ids)]
        if len(self) == 1:
            res['context'] = {'default_server_id': self.id}
        if not len(databases.ids) > 1:
            form_view_id = self.env['ir.model.data'].xmlid_to_res_id(
                'infrastructure.view_infrastructure_database_form')
            res['views'] = [(form_view_id, 'form')]
            # if 1 then we send res_id, if 0 open a new form view
            res['res_id'] = databases and databases.ids[0] or False
        return res
