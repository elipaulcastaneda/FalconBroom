from fbroom.workflow_rules import infer_action
print('infer_action:', infer_action('Normalize the username column'))
print('lowercase:', infer_action('lowercase the username'))
print('capitalize:', infer_action('capitalize the username'))
