# -*- coding: utf-8 -*-

"""
    notification
    ~~~~~~~~~~~~

    Implements notification(mail)

    :author:    Feei <feei@feei.cn>
    :homepage:  https://github.com/FeeiCN/gsil
    :license:   GPL, see LICENSE for more details.
    :copyright: Copyright (c) 2018 Feei. All rights reserved
"""
import random
import smtplib
import configparser
import hashlib
import hmac
import base64
import html
import time
import urllib.parse
import requests
import re
import traceback
from smtplib import SMTPException
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .config import get, config_path
from .log import logger


class Notification(object):
    def __init__(self, subject, to=None, cc=None):
        """
        Initialize notification class
        :param subject:
        :param to:
        """
        self.subject = subject
        if to is None:
            self.to = get('mail', 'to')
        else:
            self.to = to
        if cc is None:
            self.cc = get('mail', 'cc')
        else:
            self.cc = cc

    def notification(self, html, channels_override=None):
        """
        Send notification by configured channels
        :param html:
        :param channels_override: str/list, e.g. 'mail' or 'mail,dingtalk'
        :return:
        """
        enable_mail, enable_dingtalk, source = self._resolve_channels(channels_override)
        logger.info(f'notification channels: mail={enable_mail} dingtalk={enable_dingtalk} source={source}')

        results = []
        if enable_mail:
            mail_ret = self._send_mail(html)
            logger.info(f'notification channel result: mail={mail_ret}')
            results.append(mail_ret)
        if enable_dingtalk:
            dingtalk_ret = self._send_dingtalk(html)
            logger.info(f'notification channel result: dingtalk={dingtalk_ret}')
            results.append(dingtalk_ret)

        if len(results) == 0:
            logger.warning('No notification channels enabled, skip notification')
            return False
        return all(results)

    def _resolve_channels(self, channels_override=None):
        if isinstance(channels_override, str):
            channels = [x.strip().lower() for x in channels_override.split(',') if x.strip() != '']
            return 'mail' in channels, ('dingtalk' in channels or 'dingding' in channels), 'cli'
        if isinstance(channels_override, list):
            channels = [str(x).strip().lower() for x in channels_override if str(x).strip() != '']
            return 'mail' in channels, ('dingtalk' in channels or 'dingding' in channels), 'cli'

        channels = self._get_optional('notification', 'channels')
        if channels is None:
            enable_mail = True
            enable_dingtalk = self._get_optional('dingtalk', 'webhook') not in [None, '']
            return enable_mail, enable_dingtalk, 'default'

        channels = [x.strip().lower() for x in channels.split(',') if x.strip() != '']
        return 'mail' in channels, ('dingtalk' in channels or 'dingding' in channels), 'config'

    def _send_mail(self, html):
        """
        Send notification by mail
        :param html:
        :return:
        """
        # 随机挑选一个邮箱来发送，避免由于发送量过大导致被封
        mails = get('mail', 'mails').split(',')
        mail = random.choice(mails)
        mail_from = get('mail', 'from')
        msg = MIMEMultipart()
        msg['Subject'] = self.subject
        msg['From'] = f'{mail} <{mail_from}>'
        # 支持多用户接收邮件
        msg['To'] = self.to
        msg['Cc'] = self.cc

        text = MIMEText(html, 'html', 'utf-8')
        msg.attach(text)
        host = get('mail', 'host').strip()
        port = get('mail', 'port').strip()

        try:
            if port == '465':
                port = int(port)
                s = smtplib.SMTP_SSL(host, port)
            else:
                s = smtplib.SMTP(host, port)
                s.ehlo()
                s.starttls()
            s.ehlo()
            s.login(mail, get('mail', 'password'))
            s.sendmail(mail, self.to.split(',')+self.cc.split(','), msg.as_string())
            s.quit()
            return True
        except SMTPException:
            logger.critical('Send mail failed')
            traceback.print_exc()
            return False

    def _send_dingtalk(self, html_content):
        webhook = self._get_optional('dingtalk', 'webhook')
        if webhook in [None, '']:
            logger.warning('DingTalk webhook not configured, skip dingtalk notification')
            return False

        webhook = webhook.strip()
        secret = self._get_optional('dingtalk', 'secret')
        at_mobiles = self._get_optional('dingtalk', 'at_mobiles')
        at_all = self._get_optional('dingtalk', 'at_all', default='false').strip().lower() == 'true'
        use_markdown = self._get_optional('dingtalk', 'use_markdown', default='true').strip().lower() == 'true'

        text_content = self._html_to_text(html_content)
        markdown_content = self._html_to_markdown(html_content)
        if use_markdown:
            payload = {
                'msgtype': 'markdown',
                'markdown': {
                    'title': self.subject,
                    'text': f'### {self.subject}\n\n{markdown_content}'
                },
                'at': {
                    'atMobiles': self._split_csv(at_mobiles),
                    'isAtAll': at_all
                }
            }
        else:
            payload = {
                'msgtype': 'text',
                'text': {
                    'content': f'{self.subject}\n{text_content}'
                },
                'at': {
                    'atMobiles': self._split_csv(at_mobiles),
                    'isAtAll': at_all
                }
            }

        if secret not in [None, '']:
            timestamp = str(int(time.time() * 1000))
            sign = self._sign_dingtalk(timestamp, secret.strip())
            joiner = '&' if '?' in webhook else '?'
            webhook = f'{webhook}{joiner}timestamp={timestamp}&sign={sign}'

        try:
            r = requests.post(webhook, json=payload, timeout=10)
            data = r.json()
            if r.status_code == 200 and data.get('errcode') == 0:
                return True
            logger.critical(f'Send dingtalk failed: status={r.status_code} response={data}')
            return False
        except Exception as e:
            logger.critical(f'Send dingtalk failed: {e}')
            traceback.print_exc()
            return False

    @staticmethod
    def _split_csv(value):
        if value in [None, '']:
            return []
        return [x.strip() for x in value.split(',') if x.strip() != '']

    @staticmethod
    def _sign_dingtalk(timestamp, secret):
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return sign

    @staticmethod
    def _html_to_text(content):
        content = re.sub(r'<a\s+href=["\']([^"\']+)["\']\s*>(.*?)</a>', r'\2 (\1)', content, flags=re.IGNORECASE)
        content = content.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        content = re.sub(r'<li[^>]*>', '- ', content, flags=re.IGNORECASE)
        content = re.sub(r'</h[1-6]>', '\n', content)
        content = re.sub(r'</p>', '\n', content)
        content = re.sub(r'</li>', '\n', content, flags=re.IGNORECASE)
        content = re.sub(r'</(div|tr|table|code|hr)>', '\n', content, flags=re.IGNORECASE)
        content = re.sub(r'<[^>]+>', '', content)
        content = html.unescape(content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        return content.strip()

    @staticmethod
    def _html_to_markdown(content):
        content = re.sub(r'<a\s+href=["\']([^"\']+)["\']\s*>(.*?)</a>', r'[\2](\1)', content, flags=re.IGNORECASE)
        content = content.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        content = re.sub(r'<h1[^>]*>', '# ', content, flags=re.IGNORECASE)
        content = re.sub(r'<h2[^>]*>', '## ', content, flags=re.IGNORECASE)
        content = re.sub(r'<h3[^>]*>', '### ', content, flags=re.IGNORECASE)
        content = re.sub(r'<h4[^>]*>', '#### ', content, flags=re.IGNORECASE)
        content = re.sub(r'<h5[^>]*>', '##### ', content, flags=re.IGNORECASE)
        content = re.sub(r'<h6[^>]*>', '###### ', content, flags=re.IGNORECASE)
        content = re.sub(r'<li[^>]*>', '- ', content, flags=re.IGNORECASE)
        content = re.sub(r'</li>', '\n', content, flags=re.IGNORECASE)
        content = re.sub(r'</(h[1-6]|p|div|tr|table|code|hr)>', '\n', content, flags=re.IGNORECASE)
        content = re.sub(r'<[^>]+>', '', content)
        content = html.unescape(content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        return content.strip()

    @staticmethod
    def _get_optional(section, key, default=None):
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        if not cfg.has_section(section):
            return default
        if not cfg.has_option(section, key):
            return default
        return cfg.get(section, key)
