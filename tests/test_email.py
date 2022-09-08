import mimetypes
import smtplib
import pathlib
import keyring
import datetime
from email.message import EmailMessage
import unittest


class EmailTestCase(unittest.TestCase):
    """testing for emails"""

    def setUp(self):
        self.source_account = "sunsimulator@outlook.com"
        # store this password with
        # secret-tool store --label='Solarsim email password' application "Python keyring library" service outlook.com username sunsimulator@outlook.com
        self.pw = keyring.get_password("outlook.com", self.source_account)
        self.target_email = "sunsimulator@outlook.com"

    def test_email_send(self):
        """test sending an email"""
        msg = EmailMessage()
        msg["Subject"] = f"{datetime.datetime.utcnow()}"
        msg["From"] = f"Solar Simulator <{self.source_account}>"
        msg["To"] = self.target_email
        msg.set_content(f"{datetime.datetime.utcnow()}")

        path = pathlib.Path(__file__)

        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            # No guess could be made, or the file is encoded (compressed), so
            # use a generic bag-of-bits type.
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        with open(str(path), "rb") as fp:
            msg.add_attachment(fp.read(), maintype=maintype, subtype=subtype, filename=path.name + ".txt")

        result = None
        with smtplib.SMTP("smtp.office365.com", port=587) as smtp:
            smtp.set_debuglevel(1)

            if self.pw:
                smtp.starttls()
                smtp.login(self.source_account, self.pw)
                result = smtp.send_message(msg)  # TODO: get return code and check it
        # self.assertEqual(result, good)
