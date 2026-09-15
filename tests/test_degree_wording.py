import unittest

from jobagent.mobile.professions import credential_text
from jobagent.mobile.discovery_relevance import _CREDENTIAL


class DegreeWordingTests(unittest.TestCase):
    def test_nonacademic_degree_is_not_a_credential(self):
        for noun in ('autonomy', 'freedom', 'ownership', 'responsibility', 'independence', 'flexibility', 'complexity'):
            text = 'A high degree of ' + noun + ' while working with a friendly team.'
            with self.subTest(text=text):
                self.assertFalse(credential_text(text))
                self.assertFalse(_CREDENTIAL.search(text))

    def test_actual_credentials_in_same_sentence_remain(self):
        for text in ('A degree in finance is required.', 'A degree of Bachelor of Science is required.',
                     'Bachelor degree required with a high degree of autonomy.',
                     'NMC registration and a high degree of independence are required.',
                     'A degree is preferred, alongside a high degree of ownership.'):
            with self.subTest(text=text):
                self.assertTrue(credential_text(text))
                self.assertTrue(_CREDENTIAL.search(text))


if __name__ == '__main__': unittest.main()
